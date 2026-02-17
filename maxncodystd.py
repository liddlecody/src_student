#!/usr/bin/python
import random
import logging

from messages import Upload, Request
from util import even_split
from peer import Peer

class MaxncodyStd(Peer):
    def post_init(self):
        self.optomistic_unblock = None #peer id
        self.optomistic_unblock_round = 0 #track when last changed
    
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
        # One could look at other stuff in the history too here.
        # For example, history.downloads[round-1] (if round != 0, of course)
        # has a list of Download objects for each Download to this peer in
        # the previous round.

        if len(requests) == 0:
            chosen = []
            bws = []
        else:
            requesting_peers = set(r.requester_id for r in requests)
            #rank peers by download rate for reciprocation 
            download_totals = {}
            lookback = min(round, 2)  #up to 2 rounds
            for r in range(max(0, round - lookback), round):
                for download in history.downloads[r]:
                    if download.from_id in requesting_peers:
                        download_totals[download.from_id] = (
                            download_totals.get(download.from_id, 0) + download.blocks
                        )
            
            #sort peers by how much they gave us 
            givers = [(pid, blocks) for pid, blocks in download_totals.items() if blocks > 0]
            givers.sort(key=lambda x: x[1], reverse=True)

            #give top 3 the regular slots
            regular_unblock = [pid for pid, _ in givers[:3]]

            #optimistic unblock 
            remaining = [pid for pid in requesting_peers if pid not in regular_unblock]
    
            if (round % 3 == 0) or (self.optimistic_unblock not in remaining):
                if remaining:
                    self.optimistic_unblock = random.choice(list(remaining))
                else:
                    self.optimistic_unblock = None


            chosen = list(regular_unblock)
            if self.optimistic_unblock and self.optimistic_unblock in requesting_peers:
                if self.optimistic_unblock not in chosen:
                    chosen.append(self.optimistic_unblock)

            #split bandwidth evenly among chosen peers
            bws = even_split(self.up_bw, len(chosen))
            
            uploads = [Upload(self.id, peer_id, bw)
                    for (peer_id, bw) in zip(chosen, bws)]
                    
        return uploads
