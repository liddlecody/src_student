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

class MaxncodyPropShare(Peer):
    def post_init(self):
        print(("post_init(): %s here!" % self.id))
        self.dummy_state = dict()
        self.dummy_state["cake"] = "lie"
    
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


        # logging.debug("%s here: still need pieces %s" % (self.id, needed_pieces))

        # logging.debug("%s still here. Here are some peers:" % self.id)
        for p in peers:
            logging.debug("id: %s, available pieces: %s" % (p.id, p.available_pieces))

        # logging.debug("And look, I have my entire history available too:")
        # logging.debug("look at the AgentHistory class in history.py for details")
        # logging.debug(str(history))

        requests = []   # We'll put all the things we want here
        # Symmetry breaking is good...
        random.shuffle(needed_pieces)
        
        # Sort peers by id.  This is probably not a useful sort, but other 
        # sorts might be useful
        peers.sort(key=lambda p: p.id)
        # request all available pieces from all peers!
        # (up to self.max_requests from each)
        for peer in peers:
            av_set = set(peer.available_pieces)
            isect = av_set.intersection(np_set)
            n = min(self.max_requests, len(isect))
            # More symmetry breaking -- ask for random pieces.
            # This would be the place to try fancier piece-requesting strategies
            # to avoid getting the same thing from multiple peers at a time.
            for piece_id in random.sample(sorted(isect), n):
                # aha! The peer has this piece! Request it.
                # which part of the piece do we need next?
                # (must get the next-needed blocks in order)
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

        # Identify all unique peers currently requesting data
        requester_ids = list(set(r.requester_id for r in requests))
        
        contributions = {p_id: 0 for p_id in requester_ids}
        total_contributed = 0

        if round > 0:
            # Check all downloads I received in the previous round
            for download in history.downloads[round-1]:
                # If the download was to me, AND came from someone currently requesting
                if download.to_id == self.id and download.from_id in contributions:
                    contributions[download.from_id] += download.blocks
                    total_contributed += download.blocks

        # 90% for contributors, 10% for optimistic
        # Use self.up_bw to get actual block count
        reserve_bw = int(self.up_bw * 0.90)
        optimistic_bw = self.up_bw - reserve_bw
        
        bws = {} # {peer_id: allocated_bandwidth}

        # 90%
        if total_contributed > 0:
            for p_id in contributions:
                if contributions[p_id] > 0:
                    # (Contribution / Total) * Reserve_BW
                    share = (contributions[p_id] / total_contributed) * reserve_bw
                    bws[p_id] = int(share)
        
        # The 10%
        choice = random.choice(requester_ids)
        
        if choice in bws:
            bws[choice] += optimistic_bw
        else:
            bws[choice] = optimistic_bw

        uploads = [Upload(self.id, p_id, bw) for (p_id, bw) in bws.items() if bw > 0]
        
        return uploads
