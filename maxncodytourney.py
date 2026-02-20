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

#taking a bittyrant aprroach, but slightly more generous to encourage other tyrants to share with us, and then a more aggressive 
#alpha for reducing bandwidth spent on freeloaders. Added a bootstrapping phase to help build relationships early

class MaxncodyTourney(Peer):
    def post_init(self):
        self.d = {}  #estimated download rate from peer j
        self.u = {}  #estimated min upload to get peer j to reciprocate
        self.unblock_history = {}  #consecutive rounds j unblocked us
        self.my_unblocks = set()  #who we unchoked last round
        self.optimistic_unblock = None
        self.optimistic_unblock_round = 0

        self.alpha = 0.2   
        self.gamma = 0.1   
        self.r = 3          #consecutive rounds threshold
        self.generosity = 1.5
        self.optimistic_share = 0.15 
        self.bootstrap_rounds = 10
    
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

        if not needed_pieces:
            return []
        
        #same rarity first logic
        availability = {}
        for piece_id in needed_pieces:
            availability[piece_id] = 0
        for peer in peers:
            for piece_id in peer.available_pieces:
                if piece_id in availability:
                    availability[piece_id] += 1

        random.shuffle(needed_pieces)
        needed_pieces.sort(key=lambda p: availability[p])

        requests = []   # We'll put all the things we want here

        for peer in peers:
            av_set = set(peer.available_pieces)
            isect = av_set.intersection(np_set)
            peer_pieces = [p for p in needed_pieces if p in isect]
            n = min(self.max_requests, len(peer_pieces))
            for piece_id in peer_pieces[:n]:
                start_block = self.pieces[piece_id]
                requests.append(Request(self.id, peer.id, piece_id, start_block))

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
        #bootstrap
        requesting_peers = set(r.requester_id for r in requests)
        if round < self.bootstrap_rounds:
            download_totals = {}
            lookback = min(round, 2)
            for r_idx in range(max(0, round - lookback), round):
                for download in history.downloads[r_idx]:
                    if download.from_id in requesting_peers:
                        download_totals[download.from_id] = (
                            download_totals.get(download.from_id, 0) + download.blocks
                        )

            givers = [(pid, blocks) for pid, blocks in download_totals.items() if blocks > 0]
            givers.sort(key=lambda x: x[1], reverse=True)
            regular = [pid for pid, _ in givers[:3]]

            remaining = [pid for pid in requesting_peers if pid not in regular]
            if (round % 3 == 0) or (self.optimistic_unblock not in remaining):
                if remaining:
                    self.optimistic_unblock = random.choice(remaining)
                else:
                    self.optimistic_unblock = None

            chosen = list(regular)
            if self.optimistic_unblock and self.optimistic_unblock in requesting_peers:
                if self.optimistic_unblock not in chosen:
                    chosen.append(self.optimistic_unblock)

            if not chosen:
                chosen = list(random.sample(list(requesting_peers), min(4, len(requesting_peers))))

            bws = even_split(self.up_bw, len(chosen))
            self.my_unblocks = set(chosen)
            return [Upload(self.id, pid, bw) for pid, bw in zip(chosen, bws)]

        #estimates for new peers
        for peer in peers:
            if peer.id not in self.u:
                self.u[peer.id] = max(1, self.conf.min_up_bw / 4.0)
            if peer.id not in self.d:
                self.d[peer.id] = max(1, self.conf.min_up_bw / 4.0)
            if peer.id not in self.unblock_history:
                self.unblock_history[peer.id] = 0

        #update estimates from last round
        if round > 0:
            last_round_uploaders = {}
            for download in history.downloads[round - 1]:
                last_round_uploaders[download.from_id] = (
                    last_round_uploaders.get(download.from_id, 0) + download.blocks
                )

            for peer in peers:
                pid = peer.id
                if pid in last_round_uploaders:
                    self.d[pid] = last_round_uploaders[pid]
                    self.unblock_history[pid] = self.unblock_history.get(pid, 0) + 1
                else:
                    self.unblock_history[pid] = 0

            #update u_j based on reciprocation
            for pid in self.my_unblocks:
                if pid in last_round_uploaders:
                    if self.unblock_history.get(pid, 0) >= self.r:
                        self.u[pid] = self.u[pid] * (1 - self.gamma)
                else:
                    self.u[pid] = self.u[pid] * (1 + self.alpha)

        #reserve bandwidth for optimistic unblocking
        optimistic_bw = max(1, int(self.up_bw * self.optimistic_share))
        remaining_bw = self.up_bw - optimistic_bw

        #rank requesting peers by roi
        candidates = []
        for pid in requesting_peers:
            if pid in self.d and pid in self.u and self.u[pid] > 0:
                roi = self.d[pid] / self.u[pid]
                candidates.append((pid, roi, self.u[pid]))

        candidates.sort(key=lambda x: x[1], reverse=True)

        #assign bandwith greedily with generosity mod
        chosen = []
        bws = []

        for pid, roi, bid in candidates:
            if remaining_bw <= 0:
                break
            #be slightly generous to ensure reciprocation
            alloc = int(min(bid * self.generosity, remaining_bw))
            if alloc > 0:
                chosen.append(pid)
                bws.append(alloc)
                remaining_bw -= alloc

        #distribute leftover bw to top peers
        if remaining_bw > 0 and chosen:
            bws[0] += remaining_bw

        #optimistic unblock
        unblocked_set = set(chosen)
        remaining_requesters = [pid for pid in requesting_peers if pid not in unblocked_set]

        #pick new optimistic unblock every 3 rounds or if current one isn't requesting
        if (round % 3 == 0) or (self.optimistic_unblock not in remaining_requesters):
            if remaining_requesters:
                self.optimistic_unblock = random.choice(remaining_requesters)
            else:
                self.optimistic_unblock = None

        if self.optimistic_unblock and self.optimistic_unblock in requesting_peers:
            if self.optimistic_unblock not in unblocked_set:
                chosen.append(self.optimistic_unblock)
                bws.append(optimistic_bw)
            else:
                #already chosen via roi, redistribute optimistic bw to top peer
                if bws:
                    bws[0] += optimistic_bw
        else:
            #give bw to top peer if no one to opt unblock
            if bws:
                bws[0] += optimistic_bw

        self.my_unblocks = set(chosen)

        uploads = [Upload(self.id, peer_id, bw)
                   for (peer_id, bw) in zip(chosen, bws)]

        return uploads