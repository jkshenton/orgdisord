'''Module to enumerate ordered structures given a cif object containing disorder information.'''
import numpy as np
import warnings
from ase.spacegroup.spacegroup import SpacegroupValueError
from ase import Atoms, Atom
from ase.geometry import get_duplicate_atoms
from soprano.properties.linkage import Molecules
import spglib
import itertools
from tqdm import tqdm
from scipy.spatial.distance import pdist
import logging

logger = logging.getLogger("sodorg.enumerate")



def binary_to_idx(config, maxgroups):
    return sum([j*(maxgroups**i) for i,j in list(enumerate(reversed(config)))])

def select_configs(config, supercell, maxgroups):
    '''
    from the supercell config, return indices of each
    component primitive cell 
    e.g. 
    a 2x2x1 supercell of a Z=4 system
    would have configs of length 16.
    This function chunks them into 4
    sets of 4 and returns the integer representation
    (0-15) of each 'binary' number.
    So for example:
    [1,1,1,1, 0,0,0,0, 1,1,0,0, 0,0,0,1]
    would return
    [15, 0, 12, 1] 

    '''
    na, nb, nc = supercell
    nsuper = na * nb * nc
    chunk = len(config) // nsuper
    # should equal Z
    return [binary_to_idx(c, maxgroups) for c in  chunks(config, chunk)]

def reload_as_molecular_crystal(images, parallel=True, cheap=False):
    '''
    takes in a list of atoms,
    identifies molecules,
    unwraps molecules so that they're 'connected' 
    across periodic boundaries
    '''
    nimages = len(images)
    if cheap:
        # we assume the connectivity is the same for all images
        mols = Molecules.get(images[0])
        images_new = []
        for atoms in images:
            temp = mols[0].subset(atoms, use_cell_indices=True)
            for mol in mols[1:]:
                temp.extend(mol.subset(atoms, use_cell_indices=True))
            images_new.append(temp)
        return images_new
    else:

        if parallel:
            from multiprocessing import Pool, cpu_count
            # we can only run efficiently on about 100 structures per core
            # fewer than that and we'll just use serial
            ncores = min([cpu_count(), nimages//20])
        if parallel and ncores > 1:
            with Pool(processes=ncores) as pool:
                images = pool.map(unwrap_molecules, images)
        else:
            images = [unwrap_molecules(image) for image in images]
        return images

def unwrap_molecules(atoms):
    mols = Molecules.get(atoms)
    temp = mols[0].subset(atoms, use_cell_indices=True)
    for mol in mols[1:]:
        temp.extend(mol.subset(atoms, use_cell_indices=True))
    return temp


def chunks(lst, n, offset=0):
    """Yield successive n-sized chunks from lst."""
    if offset != 0:
    # rotate copy of list by offset
        temp = lst[offset:] + lst[:offset]
    else:
        temp = lst
    for i in np.arange(0, len(lst), n):
        yield temp[i:i + n ]

class OrderedfromDisordered:
    def __init__(self, cif, symprec=1e-4,quiet = False):
        '''
        cif must be an instance of CifParser from parse_cif_file.py

        '''
        self.quiet = quiet
        self.cif = cif
        self.symprec = symprec
        self.logger = logging.getLogger("sodorg.enumerate")
        self.logger.debug("\n\n")
        self.logger.debug("------------------------------")
        self.logger.debug("--- ENUMERATING STRUCTURES ---")
        self.logger.debug("------------------------------")


    def _get_config_symbols_coords(self, config, iassembly):
        '''
        takes config as an int (often binary) binary list
        of length Z (number of disordered molecular units in cell)
        and the index of the chosen assembly

        Args:
            config (list): list of integers representing the config
            iassembly (int): index of the assembly

        Returns:
            [
                symbols (list): list of symbols for the config
                sites (list): list of sites for the config
                tags (list): list of tags for the config
                labels (list): list of labels for the config
            ]

        '''
        # at this point Z = len(config)
        assert len(config) == self.cif.Z
        # a few aliases
        Zprime = self.cif.Zprime
        # how many disorder groups in this assembly?
        ndisordergroups = len(self.cif.disorder_groups[iassembly])
        # how many assemblies?
        nassemblies     = self.cif.nassemblies
        assert iassembly < nassemblies, "assembly index out of range"
        # fractional coordinates of disordered sites
        asymmetric_scaled_coords = self.cif.asymmetric_scaled_coords
        # symbols of the disordered sites
        asymmetric_symbols = self.cif.asymmetric_symbols
        asymmetric_labels = self.cif.asymmetric_labels

        ops = self.cif.symops

        groups = self.cif.disorder_groups[iassembly]

        if Zprime < 1:
            ngroups = int(1/Zprime)
        else:
            ngroups = len(groups)

        sites = []
        symbols = []
        tags = []
        labels = []
        # loop over groups
        for igroup in range(ndisordergroups):
            if Zprime < 1:
                # select a subset of the symmetry operations to apply
                selectedops = [chunk[c] for chunk, c in zip(chunks(ops, ngroups), config)]
            else:
                selectedops = ops
                
            # at this point len(selectedops) = len(config) 
            assert len(selectedops) == len(config)
            # loop over selected symmetry operations
            for iops, op in enumerate(selectedops):
                g = config[iops]

                # Which group should we pick? 
                if Zprime >=1:
                    group = groups[g]
                else:
                    group = groups[igroup]

                
                if len(group) > 0:
                    unique_positions = asymmetric_scaled_coords[group]
                    unique_symbols   =       asymmetric_symbols[group]
                    unique_labels    =       np.array(asymmetric_labels)[group]
                    kinds = range(len(unique_symbols))
                    # loop over site in each disorder group:
                    for kind, pos in enumerate(unique_positions):
                        # apply symmetry operation
                        rot, trans = op
                        site = np.mod(np.dot(rot, pos) + trans, 1.)                  
                        tag = g + 1 if self.cif.nassemblies == 1 else 1e2*(iassembly+1) + g + 1
                        if not sites:
                            sites.append(site)
                            symbols.append(unique_symbols[kind])
                            tags.append(tag)
                            labels.append(unique_labels[kind])
                            continue
                        t = site - sites
                        mask = np.all(
                            (abs(t) < self.symprec) | (abs(abs(t) - 1.0) < self.symprec), axis=1)
                        if np.any(mask):

                            inds = np.argwhere(mask).flatten()
                            if len(inds) > 1:
                                    raise SpacegroupValueError(
                                        'Found multiple equivalent sites!'.format())
                            else:
                                pass
                        else:
                            sites.append(site)
                            symbols.append(unique_symbols[kind])
                            tags.append(tag)
                            labels.append(unique_labels[kind])
        return [symbols, sites, tags, labels]
    
    def get_config(self, config, iassembly):
        '''
        Return a fully ordered atoms object for the specified config

        '''
        # set up base atoms object to which sites will be 
        # added (just an empty box).
        atoms = Atoms(cell=self.cif.cell, pbc=True)
        
        # add in disordered sites, ordered according to config, (tag = 1e2*(iassembly+1) + igroup + 1)
        symbols, sites, tags, labels = self._get_config_symbols_coords(config, iassembly=iassembly)
        for site, symbol, tag in zip(sites, symbols, tags):
            atom = Atom(symbol=symbol, position = self.cif.cell.T.dot(site), tag=tag)
            atoms.append(atom)
        atoms.set_array('labels', np.array(labels))

        return atoms

    def get_all_configs(self, exclude_ordered = False, correlated_assemblies=False):
        '''Generate all possible configs for the primitive cell.'''
        
        # TODO this could be made more efficient!


        # a few aliases
        Z = self.cif.Z
        Zprime = self.cif.Zprime
        nops_all = self.cif.nops
        ops = self.cif.symops
        ndisordergroups = self.cif.ndisordergroups

        if self.cif.ndisordergroups != 2:
            self.logger.warn('Warning: the number of disorder groups for an assembly group is != 2\n'
                             'This is not very well tested so proceed with caution.')
        # TODO: make sure we generalise to ndisorder groups > 2!
        # if self.cif.ndisordergroups != 1 and self.cif.ndisordergroups != 2:
        #     raise ValueError('Error: we cannot yet handle cases where ngroups != 1 or 2')
        # generate all possible ndisordergroups^Z combinations

        # set up base atoms object to which sites will be 
        # added. 
        if exclude_ordered:
        # just an empty box
            atoms = Atoms(cell=self.cif.cell, pbc=True)
        else: 
            atoms = self.cif.ordered_atoms.copy()
            # give ordered sites a tag of 0
            atoms.set_tags(0)



        # what's the final number of configs we will generate?
        nconfigs_per_assembly = [len(self.cif.disorder_groups[iassembly])**Z for iassembly in range(self.cif.nassemblies)]
        if correlated_assemblies:
            self.logger.debug('Treating the assemblies as correlated. \n'
            'This means that the same index from each assembly is chosen for each configuration.')
            if len(set(nconfigs_per_assembly)) != 1:
                raise ValueError('Error: the number of configurations per assembly is not the same for all assemblies.\n'
                                    'Please set correlated_assemblies to False.')
            nconfigs = nconfigs_per_assembly[0]
        else:
            self.logger.debug('Treating the assemblies as uncorrelated '
            '(i.e. the assemblies are independent) and we will therefore generate lots of structures!')
            nconfigs = np.product(nconfigs_per_assembly)

        self.logger.debug(f'Generating {nconfigs} configs in primitive cell.')
        if nconfigs > 1e4:
            self.logger.warn(f'Warning: {nconfigs} is a large number of configs. This may take a while!')


        




        all_configs = []
        for iassembly in range(self.cif.nassemblies):
            # normally ngroups = ndisorder groups
            ngroups = len(self.cif.disorder_groups[iassembly])
            # but when Zprime is less than 1, we need to be more careful!
            if Zprime < 1: # means more symmetry operations than we have Z
                # in this case the actual number of groups (1/Zprime) will be larger than the number of disorder groups
                assert 1/Zprime >= ngroups

                ngroups = int(1 / Zprime)
                self.logger.warn(f'Special case for nops != Z. (i.e. Zprime < 1)'
                    f'nops = {nops_all} '
                    f'Z = {Z}, '
                    f'Zprime = {Zprime}\n'
                    'This is less well-tested so proceed with caution.'
                    'If you encounter any issues, please contact the developers.')

            config_indices = itertools.product(list(range(ngroups)), repeat=Z)
            assembly_configs = [self.get_config(config_idx, iassembly) for config_idx in config_indices]
            self.logger.debug(f'Assembly {iassembly} has {len(assembly_configs)} configs')
            if all_configs:
                if correlated_assemblies:
                    # then just add the config atoms to the existing ones!
                    assert len(assembly_configs) == len(all_configs)
                    for iconfig in range(len(assembly_configs)):
                        temp = all_configs[iconfig].copy()
                        temp.extend(assembly_configs[iconfig])
                        all_configs[iconfig] = temp
                else:
                    ref_configs = [config.copy() for config in all_configs]
                    all_configs = []
                    for iconfig in range(len(ref_configs)):
                        for jconfig in range(len(assembly_configs)):
                            temp = ref_configs[iconfig].copy()
                            temp.extend(assembly_configs[jconfig])
                            all_configs.append(temp)
            else:
                # must the be first assembly -- all_configs is empty so let's start from scratch
                for config in assembly_configs:
                    temp = atoms.copy()
                    temp.extend(config)
                    all_configs.append(temp)
            
        self.logger.debug(f"Generated a total of {len(all_configs)} primitive configs")
        return all_configs


    def get_supercell_configs(self,
                              supercell, 
                              maxiters = 5000, 
                              exclude_ordered = False, 
                              random_configs=False,
                              return_configs=False,
                              molecular_crystal = True,
                              correlated_assemblies=True):
        '''
        loop over supercell cells,
        add in one of the ndisordergroups^Z configurations per cell
        if return_configs is True, return the configs as well as the supercells
        if molecular_crystal is True, then the structure is reloaded as a molecular crystal
             -- trying to keep the pieces in tact
        '''
        # pre-compute all primitive cells
        self.logger.debug('Pre-computing all primitive configurations...')
        # TODO: include a maxiters argument here?
        images = self.get_all_configs(exclude_ordered, correlated_assemblies=correlated_assemblies)
        if molecular_crystal:
            self.logger.debug(f'Reloading {len(images)} images as molecular crystals')
            if len(images) > 256:
                # this seems to be get pretty slow for large numbers of images
                self.logger.warn('Warning: reloading molecular crystals is slow for this many images.\n'
                'Consider skipping this step with the --not_molecular_crystal flag!\n'
                'We will use a cheaper method to reload the images as molecular crystals instead\n'
                ' -- assuming the connectivity is the same for each image')
                cheap_method = True
            else:
                cheap_method = False
            images = reload_as_molecular_crystal(images, cheap=cheap_method)

        # some aliases
        Z = self.cif.Z
        cell = self.cif.cell
        na, nb, nc = supercell
        # we previously just took ndisordergroups = self.cif.ndisordergroups, but 
        # this gets tricky when we have multiple assemblies etc. Safer to just take this:
        ndisordergroups = int(np.round(np.exp(np.log(len(images))/Z)))
        # total number of configs given this supercell:
        ncombinations = ndisordergroups**(Z*na*nb*nc)
        self.logger.debug(f'Found {len(images)} nimages,  {ncombinations} configs for this supercell (Z={Z}, na={na}, nb={nb}, nc={nc}), ndisordergroups={ndisordergroups}')
        # how many configs to actually generate:
        if random_configs:
            # just take maxiters
            n_configs = maxiters
        else:
            # take whichever is smallest betwee
            # maxiters and ncombinations
            n_configs = min([maxiters, ncombinations])
        
        
        # this iterator generates all 
        # possible lists of 0s and 1s etc of length Z*na*nb*nc
        all_combinations = itertools.product(list(range(ndisordergroups)), repeat=Z*na*nb*nc)

        all_supercells = []
        all_configs = []
        if random_configs:
            self.logger.info(f'Generating {n_configs} random configurations in a {supercell} supercell:')
        else:
            self.logger.info(f'Generating {n_configs} out of the {ncombinations} possible configurations in the {supercell} supercell:')

        for i in tqdm(range(n_configs), disable=self.quiet):
            if random_configs:
                config = np.random.randint(2, size=Z*na*nb*nc)
            else:
                config = np.array(all_combinations.__next__())
            # make ncells copies of the relevant config
            self.logger.debug(f'         {config}')
            supercell_atoms = Atoms(cell = cell * supercell, pbc = True)
            for icell, c in enumerate(select_configs(config, supercell=supercell, maxgroups=ndisordergroups)):
                # make a copy of prim config c
                temp = images[c].copy()
                # work out what the translation vector should be:
                ia = icell % na
                ib = (icell // na) % nb
                ic = icell // (na*nb)
                R = cell.T.dot([ia,ib,ic])
                # translate primitive copy to correct place
                temp.translate(R)
                # add this block into the supercell
                supercell_atoms.extend(temp)
            # TODO
            # check for overlapping atoms
            # cutoff = 0.25
            # dists = pdist(supercell_atoms.get_positions(), 'sqeuclidean')
            # if (dists < cutoff**2).any():
            #     continue
            # else:
            all_supercells.append(supercell_atoms)
            all_configs.append(config)

            
            # if get_duplicate_atoms(supercell_atoms, cutoff=0.5, delete=False) is None:
            # else:
            #     continue
        if return_configs:
            return all_supercells, all_configs
        return all_supercells
    









