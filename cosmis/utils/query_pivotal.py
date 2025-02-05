#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug 15 19:39:03 2020

@author: bian
"""


import pickle
import pandas as pd
from argparse import ArgumentParser


def parse_cmd_args():
    """
    Specifies arguments that users can/should give on the command line.

    Returns
    -------
    ArgumentParser
        An object of type ArgumentParser that contains info about command-linet
    arguments.

    """
    parser = ArgumentParser(
        description='''For a given list of Ensembl transcript IDs and their
        corresponding PDB chains (identified by five-letter PDB IDs), this
        scripts computes the percentage of the residues of the transcript
        that is resolved in the PDB structure.'''
    )
    parser.add_argument(
        '-q', '--query', dest='query', required=True, type=str,
        help='''Path to the file containing a list of queries formatted as
        UniProtID and position pairs. One pair on each row and the fields of 
        each pair is separated by a comma.'''
    )
    parser.add_argument(
        '-o', '--output', dest='output', required=True, type=str,
        help='Name of the disk file to store the computed percentages.'
    )
    parser.add_argument(
        '-p', '--pivotal', dest='pivotal_data', required=True, type=str,
        help='''Path to the pivotal pickle file.'''
    )
    
    return parser.parse_args()


def main():
    # parse command-line arguments
    cmd_args = parse_cmd_args()
    
    # read in pivotal data
    with open(cmd_args.pivotal_data, 'rb') as ipf:
        pivotal_data = pickle.load(ipf)
        
    # read in queries
    query_strs = []
    with open(cmd_args.query, 'rt') as ipf:
        for l in ipf:
            x, y = l.strip().split(',')
            query_strs.append(
                'uniprot == ' + '"' + x + '" and position == "' + y + '"'
            )
            
    # now query pivotal data
    hits = []
    for q in query_strs:
        print('Now query', q, 'from Pivotal data.')
        hit = pivotal_data.query(q)
        hits.append(hit)
        
    # concatenate hits into a single data frame
    hits_df = pd.concat(hits)
    
    # now write hits to disk file
    with open(cmd_args.output, 'wt') as opf:
        hits_df.to_csv(opf, index=False)
        

if __name__ == '__main__':
    main()
