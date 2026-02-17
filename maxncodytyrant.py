#!/usr/bin/python

# This is a dummy peer that just illustrates the available information your peers 
# have available.

# You'll want to copy this file to AgentNameXXX.py for various versions of XXX,
# probably get rid of the silly logging messages, and then add more logic.

import random
import logging

from messages import Upload, Request
from util import even_split
from peer import Peer

#Assumptions
   #u_j and d_j chosen to simulate an equal split per slot allocation similar to textbooks values, and an roi of 1 
   #Each peer gets exactly their upload bid, so there may be more or less than 4 peers unblocked
   #requests rarest first, from as many peers as possible
class MaxncodyTyrant(Peer):
    def post_init(self):
        self.d = {}  #estimated download rate from peer j
        self.u = {}  #estimated min upload to get peer j to reciprocate
        self.unblock_history = {}  #track consecutive rounds j has unblocked us
        self.my_unblock = {}  #track who we unblocked last round

        self.alpha = 0.2 #currently using vals from textbook
        self.gamma = 0.1
        self.r = 3  #consecutive rounds threshold for decreasing u_j
    
    #same logic for requests as reference client
    def requests(self, peers, history):
        """
        peers: available info about the peers (who has what pieces)
        history: what's happened so far as far as this peer can see

        returns: a list of Request() objects

        This will be called after update_pieces() with the most recent state.
        """
        needed = lambda i: self.pieces[i] < self.conf.blocks_per_piece
        needed_pieces = list(filter(needed, list(range(len(self.pieces)))))
        np_set = set(needed_pieces)  # sets support fast intersection ops.


        #avaliability for rarity sorting
        availability = {}

        for piece_id in needed_pieces:
            availability[piece_id] = 0
        for peer in peers:
            for piece_id in peer.available_pieces:
                if piece_id in availability:
                    availability[piece_id] += 1

        #sort by rarity, use random to tiebreak
        random.shuffle(needed_pieces)
        needed_pieces.sort(key=lambda p: availability[p])

        requests = []   # We'll put all the things we want here
        # Symmetry breaking is good...
        
        # request all available pieces from all peers!
        # (up to self.max_requests from each)
        for peer in peers:
            av_set = set(peer.available_pieces)
            isect = av_set.intersection(np_set)
            #filter on rarest first
            peer_pieces = [p for p in needed_pieces if p in isect]

            n = min(self.max_requests, len(peer_pieces))
            for piece_id in peer_pieces[:n]:
                start_block = self.pieces[piece_id]
                r = Request(self.id, peer.id, piece_id, start_block)
                requests.append(r)
        return requests

    def uploads(self, requests, peers, history):
        """
        requests -- a list of the requests for this peer for this round
        peers -- available info about all the peers
        history -- history for all previous rounds

        returns: list of Upload objects.

        In each round, this will be called after requests().
        """

        round = history.current_round()

        if len(requests) == 0:
            return []
        
        requesting_peers = set(r.requester_id for r in requests)

        #init u_j and d_j for new peers
        for peer in peers:
            #use an equal split estimate min bw / 4 slots
            if peer.id not in self.u:
                self.u[peer.id] = self.conf.min_up_bw / 4.0
            if peer.id not in self.d:
                self.d[peer.id] = self.conf.min_up_bw / 4.0
            if peer.id not in self.unblock_history:
                self.unblock_history[peer.id] = 0    

        #update d_j from download history
        if round > 0:
            last_round_uploaders = {}
            for download in history.downloads[round - 1]:
                last_round_uploaders[download.from_id] = (
                    last_round_uploaders.get(download.from_id, 0) + download.blocks
                )

            #update d_j and unblock tracking
            for peer in peers:
                pid = peer.id
                if pid in last_round_uploaders:
                    self.d[pid] = last_round_uploaders[pid]
                    self.unblock_history[pid] = self.unblock_history.get(pid, 0) + 1
                else:
                    self.unblock_history[pid] = 0

            #update u_j based on reciprocation
            for pid in self.my_unblock:
                if pid in last_round_uploaders:
                    #if they reciprocated for r consecutive rounds decrease u_j
                    if self.unblock_history.get(pid, 0) >= self.r:
                        self.u[pid] = self.u[pid] * (1 - self.gamma)
                else:
                    #we unblocked them but they didn't reciprocate increase u_j
                    self.u[pid] = self.u[pid] * (1 + self.alpha)

            #rank requesters based on ROI
            candidates = []
            for pid in requesting_peers:
                if pid in self.d and pid in self.u and self.u[pid] > 0:
                    roi = self.d[pid] / self.u[pid]
                    candidates.append((pid, roi, self.u[pid]))

        candidates.sort(key=lambda x: x[1], reverse=True)

        #assign bandwidth greedily
        chosen = []
        bws = []
        remaining_bw = self.up_bw

        for pid, roi, bid in candidates: 
            if remaining_bw <= 0:
                break
            alloc = int(min(bid, remaining_bw))
            if alloc > 0:
                chosen.append(pid)
                bws.append(alloc)
                remaining_bw -= alloc

                self.my_unblock = set(chosen)

        uploads = [Upload(self.id, peer_id, bw)
        for (peer_id, bw) in zip(chosen, bws)]


        return uploads
