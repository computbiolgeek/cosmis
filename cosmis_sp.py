#!/usr/bin/env python3

import csv
import gzip
import json
import os
import sys
import logging
import warnings
from argparse import ArgumentParser
from collections import defaultdict

import numpy as np
from Bio import SeqIO
from Bio import BiopythonWarning
from Bio.PDB import PDBParser, is_aa
from Bio.SeqUtils import seq1

from cosmis.utils import pdb_utils, seq_utils
warnings.simplefilter('ignore', BiopythonWarning)


def parse_cmd():
    """

    Returns
    -------

    """
    parser = ArgumentParser()
    parser.add_argument('-c', '--config', dest='config', required=True,
                        type=str, help='A JSON file specifying options.')
    parser.add_argument('-u', '--uniprot-id', dest='uniprot_id', required=True,
                        type=str, help='''UniProt ID of the protein for which 
                        to compute a COSMIS profile.''')
    parser.add_argument('-p', '--pdb', dest='pdb_file', required=True,
                        type=str, help='''PDB file containing the structure
                        of the protein structure of the given transcript.''')
    parser.add_argument('-o', '--output', dest='output_file', required=True,
                        type=str, help='''Output file to store the COSMIS scores
                        of the protein.''')
    parser.add_argument('--multimer', dest='multimer', action='store_true',
                        default=False, help='Is the input PDB file a multimer?')
    parser.add_argument('--chain', dest='pdb_chain', default='A', type=str,
                        help='Chain ID of the subunit in the PBD file.')
    parser.add_argument('-w', '--overwrite', dest='overwrite', required=False,
                        action='store_true', help='''Whether to overwrite 
                        already computed MTR3D scores.''')
    parser.add_argument('-v', '--verbose', dest='verbose', required=False,
                        action='store_true', help='''Whether to output verbose
                        data: number of contacting residues and number of 
                        missense and synonymous variants in the neighborhood
                        of the mutation site.''')
    parser.add_argument('-l', '--log', dest='log', default='cosmis.log',
                        help='''The file to which to write detailed computing logs.''')
    return parser.parse_args()


def get_ensembl_accession(record):
    """

    Parameters
    ----------
    record

    Returns
    -------

    """
    parts = record.id.split('.')
    return parts[0]


def get_uniprot_accession(record):
    """

    Parameters
    ----------
    record

    Returns
    -------

    """
    parts = record.id.split('|')
    return parts[1]


def parse_config(config):
    """

    Parameters
    ----------
    config

    Returns
    -------

    """
    with open(config, 'rt') as ipf:
        configs = json.load(ipf)

    # do necessary sanity checks before return
    return configs


def count_variants(variants):
    """
    Collects the statistics about position-specific counts of missense and
    synonymous variants.

    Parameters
    ----------
    variants : list
        A list of variant identifiers: ['A123B', 'C456D']

    Returns
    -------
    dict
        A dictionary where the key is amino acid position and the value is
        the number of variants at this position. One dictionary for missense
        variants and one dictionary for synonymous variants.

    """
    #
    missense_counts = defaultdict(int)
    synonymous_counts = defaultdict(int)
    for variant in variants:
        vv, _, _ = variant
        w = vv[0]  # wild-type amino acid
        v = vv[-1]  # mutant amino acid
        pos = vv[1:-1]  # position in the protein sequence
        # only consider rare variants
        # if int(ac) / int(an) > 0.001:
        #    continue
        if w != v:  # missense variant
            missense_counts[int(pos)] += 1
        else:  # synonymous variant
            synonymous_counts[int(pos)] += 1
    return missense_counts, synonymous_counts


def retrieve_data(uniprot_id, enst_ids, pep_dict, cds_dict, variant_dict):
    """
    """
    pep_seq = pep_dict[uniprot_id]

    valid_ensts = []
    for enst_id in enst_ids:
        try:
            cds_seq = cds_dict[enst_id].seq
        except KeyError:
            continue
        # skip if the CDS is incomplete
        if not seq_utils.is_valid_cds(cds_seq):
            print('Error: Invalid CDS.'.format(enst_id))
            continue
        if len(pep_seq) == len(cds_seq) // 3 - 1:
            valid_ensts.append(enst_id)
    if not valid_ensts:
        raise ValueError(
            'Error: {} are not compatible with {}.'.format(enst_ids, uniprot_id)
        )

    if len(valid_ensts) == 1:
        enst_id = valid_ensts[0]
        cds = cds_dict[enst_id].seq
        if enst_id not in variant_dict.keys():
           raise KeyError('Error: No record for {} in gnomAD.'.format(uniprot_id))
        variants = variant_dict[enst_id]['variants']
        return enst_id, pep_seq, cds, variants

    # if multiple transcripts are valid
    # get the one with most variable positions
    max_len = 0
    right_enst = ''
    for enst_id in valid_ensts:
        try:
            var_pos = len(variant_dict[enst_id]['variants'])
        except KeyError:
            continue
        if max_len < var_pos:
            max_len = var_pos
            right_enst = enst_id
    cds = cds_dict[right_enst].seq
    variants = variant_dict[right_enst]['variants']

    return right_enst, pep_seq, cds, variants


def get_dataset_headers():
    """
    Returns column name for each feature of the dataset. Every time a new
    features is added, this function needs to be updated.

    Returns
    -------

    """
    header = [
        'uniprot_id', 'enst_id', 'uniprot_pos', 'uniprot_aa',
        'seq_separations', 'num_contacts', 'syn_var_sites',
        'total_syn_sites', 'mis_var_sites', 'total_mis_sites',
        'cs_syn_poss', 'cs_mis_poss', 'cs_gc_content', 'cs_syn_prob',
        'cs_syn_obs', 'cs_mis_prob', 'cs_mis_obs', 'mis_pmt_mean', 'mis_pmt_sd',
        'mis_p_value', 'syn_pmt_mean', 'syn_pmt_sd', 'syn_p_value',
        'enst_syn_obs', 'enst_mis_obs', 'enst_syn_exp', 'enst_mis_exp', 'uniprot_length'
    ]
    return header


def load_datasets(configs):
    """

    Parameters
    ----------
    configs

    Returns
    -------

    """
    # ENSEMBL cds
    print('Reading ENSEMBL CDS database ...')
    with gzip.open(configs['ensembl_cds'], 'rt') as cds_handle:
        enst_cds_dict = SeqIO.to_dict(
            SeqIO.parse(cds_handle, format='fasta'),
            key_function=get_ensembl_accession
        )

    # ENSEMBL peptide sequences
    print('Reading UniProt protein sequence database ...')
    with gzip.open(configs['uniprot_pep'], 'rt') as pep_handle:
        pep_dict = SeqIO.to_dict(
            SeqIO.parse(pep_handle, format='fasta'),
            key_function=get_uniprot_accession
        )

    # parse gnomad transcript-level variants
    print('Reading gnomAD variant database ...')
    with open(configs['gnomad_variants'], 'rt') as variant_handle:
        # transcript_variants will be a dict of dicts where major version
        # ENSEMBL transcript IDs are the first level keys and "ccds", "ensp",
        # "swissprot", "variants" are the second level keys. The value of each
        # second-level key is a Python list.
        enst_variants = json.load(variant_handle)

    # parse the file that maps Ensembl transcript IDs to PDB IDs
    with open(configs['uniprot_to_enst'], 'rt') as ipf:
        uniprot_to_enst = json.load(ipf)

    # get transcript mutation probabilities and variant counts
    print('Reading transcript mutation probabilities and variant counts ...')
    enst_mp_counts = seq_utils.read_enst_mp_count(configs['enst_mp_counts'])

    return (enst_cds_dict, pep_dict, enst_variants, uniprot_to_enst, enst_mp_counts)


def main():
    """

    Returns
    -------

    """
    # parse command-line arguments
    args = parse_cmd()

    # configure the logging system
    logging.basicConfig(
        filename=args.log,
        level=logging.INFO,
        filemode='w',
        format='%(levelname)s:%(asctime)s:%(message)s'
    )

    # parse configuration file
    configs = parse_config(args.config)

    # load datasets
    cds_dict, pep_dict, variant_dict, uniprot_to_enst, enst_mp_counts = load_datasets(configs)

    # compute COSMIS scores
    pdb_file = args.pdb_file
    pdb_chain = args.pdb_chain
    uniprot_id = args.uniprot_id
    if os.path.exists(args.output_file) and not args.overwrite:
        print(args.output_file + ' already exists. Skipped.')
        sys.exit(0)
    print('Processing protein %s' % uniprot_id)

    cosmis = []
    try:
        enst_ids = uniprot_to_enst[uniprot_id]
    except KeyError:
        logging.critical(
            'No transcript IDs were mapped to {}.'.format(uniprot_id)
        )
        sys.exit(1)
    try:
        right_enst, pep_seq, cds, variants = retrieve_data(
            uniprot_id, enst_ids, pep_dict, cds_dict, variant_dict
        )
    except ValueError:
        logging.critical('No valid CDS found for {}.'.format(uniprot_id))
        sys.exit(1)
    except KeyError:
        logging.critical('No transcript record found for {} in gnomAD.'.format(uniprot_id))
        sys.exit(1)

    # print message
    print('Computing COSMIS features for:', uniprot_id, right_enst, pdb_file)

    pdb_parser = PDBParser(PERMISSIVE=1)
    structure = pdb_parser.get_structure(id='NA', file=pdb_file)
    chain = structure[0][pdb_chain]

    if chain is None:
        print(
            'ERROR: %s not found in structure: %s!' % (pdb_chain, pdb_file)
        )
        sys.exit(1)

    if args.multimer:
        all_aa_residues = [aa for aa in structure[0].get_residues() if is_aa(aa, standard=True)]
    else:
        all_aa_residues = [aa for aa in chain.get_residues() if is_aa(aa, standard=True)]
    if not all_aa_residues:
        logging.critical(
            'No confident residues found in the given structure'
            '{} for {}.'.format(pdb_file, uniprot_id)
        )
        sys.exit(1)
    all_contacts = pdb_utils.search_for_all_contacts(
        all_aa_residues, radius=8
    )

    # calculate expected counts for each codon
    cds = cds[:-3]  # remove the stop codon
    codon_mutation_rates = seq_utils.get_codon_mutation_rates(cds)
    all_cds_ns_counts = seq_utils.count_poss_ns_variants(cds)
    cds_ns_sites = seq_utils.count_ns_sites(cds)

    # tabulate variants at each site
    # missense_counts and synonymous_counts are dictionary that maps
    # amino acid positions to variant counts
    missense_counts, synonymous_counts = count_variants(variants)

    # convert variant count to site variability
    site_variability_missense = {
        pos: 1 for pos, _ in missense_counts.items()
    }
    site_variability_synonymous = {
        pos: 1 for pos, _ in synonymous_counts.items()
    }

    # compute the total number of missense variants
    try:
        total_exp_mis_counts = enst_mp_counts[right_enst][-1]
        total_exp_syn_counts = enst_mp_counts[right_enst][-2]
    except KeyError:
        print(
            'Transcript {} not found in {}'.format(right_enst, configs['enst_mp_counts'])
        )
        sys.exit(1)

    # permutation test
    codon_mis_probs = [x[1] for x in codon_mutation_rates]
    codon_syn_probs = [x[0] for x in codon_mutation_rates]
    mis_p = codon_mis_probs / np.sum(codon_mis_probs)
    syn_p = codon_syn_probs / np.sum(codon_syn_probs)
    mis_pmt_matrix = seq_utils.permute_variants(
        total_exp_mis_counts, len(pep_seq), mis_p
    )
    syn_pmt_matrix = seq_utils.permute_variants(
        total_exp_syn_counts, len(pep_seq), syn_p
    )

    # index all contacts by residue ID
    indexed_contacts = defaultdict(list)
    for c in all_contacts:
        indexed_contacts[c.get_res_a()].append(c.get_res_b())
        indexed_contacts[c.get_res_b()].append(c.get_res_a())

    valid_case = True
    for seq_pos, seq_aa in enumerate(pep_seq, start=1):
        try:
            res = chain[seq_pos]
        except KeyError:
            print('PDB file is missing residue:', seq_aa, 'at', seq_pos)
            continue
        pdb_aa = seq1(res.get_resname())
        if seq_aa != pdb_aa:
            print('Residue in UniProt sequence did not match that in PDB at', seq_pos)
            print('Skip to the next protein ...')
            valid_case = False
            break

        contact_res = indexed_contacts[res]
        num_contacts = len(contact_res)
        contacts_pdb_pos = [r.get_id()[1] for r in contact_res]
        seq_seps = ';'.join(
            str(x) for x in [i - seq_pos for i in contacts_pdb_pos]
        )

        mis_var_sites = site_variability_missense.setdefault(seq_pos, 0)
        total_mis_sites = cds_ns_sites[seq_pos - 1][0]
        syn_var_sites = site_variability_synonymous.setdefault(seq_pos, 0)
        total_syn_sites = cds_ns_sites[seq_pos - 1][1]
        total_missense_obs = missense_counts.setdefault(seq_pos, 0)
        total_synonymous_obs = synonymous_counts.setdefault(seq_pos, 0)
        total_missense_poss = all_cds_ns_counts[seq_pos - 1][0]
        total_synonyms_poss = all_cds_ns_counts[seq_pos - 1][1]
        total_synonymous_rate = codon_mutation_rates[seq_pos - 1][0]
        total_missense_rate = codon_mutation_rates[seq_pos - 1][1]
        for j in contacts_pdb_pos:
            # count the total # observed variants in contacting residues
            mis_var_sites += site_variability_missense.setdefault(j, 0)
            syn_var_sites += site_variability_synonymous.setdefault(j, 0)
            total_missense_obs += missense_counts.setdefault(j, 0)
            total_synonymous_obs += synonymous_counts.setdefault(j, 0)

            # count the total # expected variants
            try:
                total_missense_poss += all_cds_ns_counts[j - 1][0]
                total_synonyms_poss += all_cds_ns_counts[j - 1][1]
                total_synonymous_rate += codon_mutation_rates[j - 1][0]
                total_missense_rate += codon_mutation_rates[j - 1][1]
                total_mis_sites += cds_ns_sites[j - 1][0]
                total_syn_sites += cds_ns_sites[j - 1][1]
            except IndexError:
                valid_case = False
                break
        if not valid_case:
            break

        try:
            seq_context = seq_utils.get_codon_seq_context(
                contacts_pdb_pos + [seq_pos], cds
            )
        except IndexError:
            break

        # compute the GC content of the sequence context
        if len(seq_context) == 0:
            print('No nucleotides were found in sequence context!')
            continue
        gc_fraction = seq_utils.gc_content(seq_context)

        mis_pmt_mean, mis_pmt_sd, mis_p_value = seq_utils.get_permutation_stats(
            mis_pmt_matrix, contacts_pdb_pos + [seq_pos], total_missense_obs
        )
        syn_pmt_mean, syn_pmt_sd, syn_p_value = seq_utils.get_permutation_stats(
            syn_pmt_matrix, contacts_pdb_pos + [seq_pos], total_synonymous_obs
        )
        # compute the fraction of expected missense variants
        cosmis.append(
            [
                uniprot_id, right_enst, seq_pos, seq_aa, seq_seps,
                num_contacts + 1,
                syn_var_sites,
                '{:.3f}'.format(total_syn_sites),
                mis_var_sites,
                '{:.3f}'.format(total_mis_sites),
                total_synonyms_poss,
                total_missense_poss,
                '{:.3f}'.format(gc_fraction),
                '{:.3e}'.format(total_synonymous_rate),
                total_synonymous_obs,
                '{:.3e}'.format(total_missense_rate),
                total_missense_obs,
                '{:.3f}'.format(mis_pmt_mean),
                '{:.3f}'.format(mis_pmt_sd),
                '{:.3e}'.format(mis_p_value),
                '{:.3f}'.format(syn_pmt_mean),
                '{:.3f}'.format(syn_pmt_sd),
                '{:.3e}'.format(syn_p_value),
                enst_mp_counts[right_enst][2],
                enst_mp_counts[right_enst][4],
                total_exp_syn_counts,
                total_exp_mis_counts,
                len(pep_seq)
            ]
        )

    if not valid_case:
        sys.exit(1)

    with open(
            file=args.output_file,
            mode='wt'
    ) as opf:
        csv_writer = csv.writer(opf, delimiter='\t')
        csv_writer.writerow(get_dataset_headers())
        csv_writer.writerows(cosmis)


if __name__ == '__main__':
    main()
