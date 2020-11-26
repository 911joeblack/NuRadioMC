import numpy as np
import h5py
from scipy import interpolate
from scipy.interpolate import interp1d
import glob
from six import iteritems
import json
import os
import copy
import time

from NuRadioReco.utilities import units

import logging
logger = logging.getLogger("Veff")
logging.basicConfig()
logger.setLevel(logging.INFO)


# collection of utility function regarding the calculation of the effective volume of a neutrino detector
def remove_duplicate_triggers(triggered, gids):
    """
    remove duplicate entried from triggered array
    
    The hdf5 file contains a line per shower. One event can contain many showers, i.e. if we count all triggeres
    from all showers we overestimate the effective volume. This function modifies the triggered array such
    that it contains not more than one True value for each event group. 
    
    Parameters
    ----------
    triggered: array of bools
        
    gids: array of ints
        the event group ids
        
    Returns: array of floats
        the corrected triggered array
    """
    gids = np.array(gids)
    triggered = np.array(triggered)
    uids, unique_mask, inv_mask, counts = np.unique(gids, return_index=True, return_inverse=True, return_counts=True)
    for gid in uids[counts > 1]:
        mask = gids == gid
        if(np.sum(triggered[mask]) > 1):
            idx = np.arange(len(triggered), dtype=np.int)[mask][triggered[mask] == True][1:]
            triggered[idx] = False
    return triggered


def FC_limits(counts):

    """
    Returns the 68% confidence belt for a number of counts, using the
    Feldman-Cousins method.

    Parameters
    ----------
    counts: integer or float
        Number of counts. Can be non-integer (weighted counts)

    Returns
    -------
    (low_limit, upper_limit): float tuple
        Lower and upper limits for the confidence belt.
    """

    count_list = np.arange(0, 21)
    lower_limits = [0.00,
                    0.37,
                    0.74,
                    1.10,
                    2.34,
                    2.75,
                    3.82,
                    4.25,
                    5.30,
                    6.33,
                    6.78,
                    7.81,
                    8.83,
                    9.28,
                    10.30,
                    11.32,
                    12.33,
                    12.79,
                    13.81,
                    14.82,
                    15.83]
    upper_limits = [1.29,
                    2.75,
                    4.25,
                    5.30,
                    6.78,
                    7, 81,
                    9.28,
                    10.30,
                    11.32,
                    12.79,
                    13.81,
                    14.82,
                    16.29,
                    17.30,
                    18.32,
                    19.32,
                    20.80,
                    21.81,
                    22.82,
                    25.30]

    if counts > count_list[-1]:

        return (counts - np.sqrt(counts), counts + np.sqrt(counts))

    elif counts < 0:

        return (0.00, 1.29)

    low_interp = interp1d(count_list, lower_limits)
    up_interp = interp1d(count_list, upper_limits)

    return (low_interp(counts), up_interp(counts))


def get_Veff_water_equivalent(Veff, density_medium=0.917 * units.g / units.cm ** 3, density_water=1 * units.g / units.cm ** 3):
    """
    convenience function to converte the effective volume of a medium with density `density_medium` to the
    water equivalent effective volume

    Parameters
    ----------
    Veff: float or array
        the effective volume
    dentity_medium: float (optional)
        the density of the medium of the Veff simulation (default deep ice)
    density water: float (optional)
        the density of water

    Returns: water equivalen effective volume
    """
    return Veff * density_medium / density_water


def get_Veff_Aeff_single(filename, trigger_names, trigger_names_dict, trigger_combinations, deposited, station, veff_aeff="veff"):
    """
    calculates the effective volume or effective area from surface muons from a single NuRadioMC hdf5 file

    the effective volume is NOT normalized to a water equivalent. It is also NOT multiplied with the solid angle (typically 4pi).

    Parameters
    ----------
    filename: string
        filename of the hdf5 file
    trigger_names: list of strings
        list of the trigger names contained in the file
    trigger_names_dict: dict
        map from trigger name to index
    trigger_combinations: dict, optional
        keys are the names of triggers to calculate. Values are dicts again:
            * 'triggers': list of strings
                name of individual triggers that are combined with an OR
            the following additional options are optional
            * 'efficiency': dict
                allows to apply an (analysis) efficiency cut for calculating effective volumes
                * 'func': function
                    a function that paramaterized the efficiency as a function of SNR (=Vmax/Vrms)
                * 'channel_ids': array on ints
                    the channels for which the maximum signal amplitude should be determined
                * 'scale': float
                    rescaling of the efficiency curve by SNR' = SNR * scale
            * 'n_reflections': int
                the number of bottom reflections of the ray tracing solution that likely triggered
                assuming that the solution with the shortest travel time caused the trigger, only considering channel 0

    station: int
        the station that should be considered
    veff_aeff: string
        specifiy if the effective volume or the effective area for surface muons is calculated
        can be 
        * "veff" (default)
        * "aeff_surface_muons"

    Returns
    ----------
    list of dictionary. Each file is one entry. The dictionary keys store all relevant properties
    """
    if(veff_aeff not in ["veff", "aeff_surface_muons"]):
        raise AttributeError(f"the paramter `veff_aeff` needs to be one of either `veff` or `aeff_surface_muons`")
    fin = h5py.File(filename, 'r')
    logger.warning(f"processing file  {filename}")
    out = {}
    Emin = fin.attrs['Emin']
    Emax = fin.attrs['Emax']
    E = 10 ** (0.5 * (np.log10(Emin) + np.log10(Emax)))
    out['energy'] = E
    out['energy_min'] = Emin
    out['energy_max'] = Emax

    # calculate effective
    thetamin = 0
    thetamax = np.pi
    phimin = 0
    phimax = 2 * np.pi
    if('thetamin' in fin.attrs):
        thetamin = fin.attrs['thetamin']
    if('thetamax' in fin.attrs):
        thetamax = fin.attrs['thetamax']
    if('phimin' in fin.attrs):
        phimin = fin.attrs['phimin']
    if('phimax' in fin.attrs):
        phimax = fin.attrs['phimax']
    if(veff_aeff == "veff"):
        volume_proj_area = fin.attrs['volume']
    elif(veff_aeff == "aeff_surface_muons"):
        area = fin.attrs['area']
        # The used area must be the projected area, perpendicular to the incoming
        # flux, which leaves us with the following correction. Remember that the
        # zenith bins must be small for the effective area to be correct.
        volume_proj_area = area * 0.5 * (np.abs(np.cos(thetamin)) + np.abs(np.cos(thetamax)))
    else:
        raise AttributeError(f"attributes do neither contain volume nor area")

    Vrms = fin.attrs['Vrms']

    # Solid angle needed for the effective volume calculations
    out['domega'] = np.abs(phimax - phimin) * np.abs(np.cos(thetamin) - np.cos(thetamax))
    out['thetamin'] = thetamin
    out['thetamax'] = thetamax
    out['deposited'] = deposited
    out[veff_aeff] = {}
    out['n_triggered_weighted'] = {}
    out['SNRs'] = {}

    if('weights' not in fin.keys()):
        logger.warning(f"file {filename} is empty")
        return out
    weights = np.array(fin['weights'])
    triggered = np.array(fin['triggered'])
    n_events = fin.attrs['n_events']

    if('trigger_names' in fin.attrs):
        if(np.any(trigger_names != fin.attrs['trigger_names'])):
            if(triggered.size == 0 and fin.attrs['trigger_names'].size == 0):
                logger.warning("file {} has no triggering events. Using trigger names from another file".format(filename))
            else:
                logger.error("file {} has inconsistent trigger names: {}".format(filename, fin.attrs['trigger_names']))
                raise
    else:
        logger.warning(f"file {filename} has no triggering events. Using trigger names from a different file: {trigger_names}")

    if(triggered.size == 0):
        FC_low, FC_high = FC_limits(0)
        Veff_low = volume_proj_area * FC_low / n_events
        Veff_high = volume_proj_area * FC_high / n_events
        for iT, trigger_name in enumerate(trigger_names):
            out[veff_aeff][trigger_name] = [0, 0, 0, Veff_low, Veff_high]
        for trigger_name, values in iteritems(trigger_combinations):
            out[veff_aeff][trigger_name] = [0, 0, 0, Veff_low, Veff_high]
    else:
        for iT, trigger_name in enumerate(trigger_names):
            triggered = np.array(fin['multiple_triggers'][:, iT], dtype=np.bool)
            triggered = remove_duplicate_triggers(triggered, fin['event_group_ids'])
            Veff = volume_proj_area * np.sum(weights[triggered]) / n_events
            Veff_error = 0
            if(np.sum(weights[triggered]) > 0):
                Veff_error = Veff / np.sum(weights[triggered]) ** 0.5
            FC_low, FC_high = FC_limits(np.sum(weights[triggered]))
            Veff_low = volume_proj_area * FC_low / n_events
            Veff_high = volume_proj_area * FC_high / n_events
            out[veff_aeff][trigger_name] = [Veff, Veff_error, np.sum(weights[triggered]), Veff_low, Veff_high]

        for trigger_name, values in iteritems(trigger_combinations):
            indiv_triggers = values['triggers']
            triggered = np.zeros_like(fin['multiple_triggers'][:, 0], dtype=np.bool)
            if(isinstance(indiv_triggers, str)):
                triggered = triggered | np.array(fin['multiple_triggers'][:, trigger_names_dict[indiv_triggers]], dtype=np.bool)
            else:
                for indiv_trigger in indiv_triggers:
                    triggered = triggered | np.array(fin['multiple_triggers'][:, trigger_names_dict[indiv_trigger]], dtype=np.bool)
            if 'triggerAND' in values:
                triggered = triggered & np.array(fin['multiple_triggers'][:, trigger_names_dict[values['triggerAND']]], dtype=np.bool)
            if 'notriggers' in values:
                indiv_triggers = values['notriggers']
                if(isinstance(indiv_triggers, str)):
                    triggered = triggered & ~np.array(fin['multiple_triggers'][:, trigger_names_dict[indiv_triggers]], dtype=np.bool)
                else:
                    for indiv_trigger in indiv_triggers:
                        triggered = triggered & ~np.array(fin['multiple_triggers'][:, trigger_names_dict[indiv_trigger]], dtype=np.bool)
            if('min_sigma' in values.keys()):
                if(isinstance(values['min_sigma'], list)):
                    if(trigger_name not in out['SNR']):
                        out['SNR'][trigger_name] = {}
                    masks = np.zeros_like(triggered)
                    for iS in range(len(values['min_sigma'])):
                        As = np.max(np.nan_to_num(fin['max_amp_ray_solution']), axis=-1)  # we use the this quantity because it is always computed before noise is added!
                        As_sorted = np.sort(As[:, values['channels'][iS]], axis=1)
                        # the smallest of the three largest amplitudes
                        max_amplitude = As_sorted[:, -values['n_channels'][iS]]
                        mask = np.sum(As[:, values['channels'][iS]] >= (values['min_sigma'][iS] * Vrms), axis=1) >= values['n_channels'][iS]
                        masks = masks | mask
                        out['SNR'][trigger_name][iS] = max_amplitude[mask] / Vrms
                    triggered = triggered & masks
                else:
                    As = np.max(np.nan_to_num(fin['max_amp_ray_solution']), axis=-1)  # we use the this quantity because it is always computed before noise is added!

                    As_sorted = np.sort(As[:, values['channels']], axis=1)
                    max_amplitude = As_sorted[:, -values['n_channels']]  # the smallest of the three largest amplitudes

                    mask = np.sum(As[:, values['channels']] >= (values['min_sigma'] * Vrms), axis=1) >= values['n_channels']
                    out['SNR'][trigger_name] = As_sorted[mask] / Vrms
                    triggered = triggered & mask
            if('ray_solution' in values.keys()):
                As = np.array(fin['max_amp_ray_solution'])
                max_amps = np.argmax(As[:, values['ray_channel']], axis=-1)
                sol = np.array(fin['ray_tracing_solution_type'])
                mask = np.array([sol[i, values['ray_channel'], max_amps[i]] == values['ray_solution'] for i in range(len(max_amps))], dtype=np.bool)
                triggered = triggered & mask

            if('n_reflections' in values.keys()):
                if(np.sum(triggered)):
                    As = np.array(fin[f'station_{station:d}/max_amp_ray_solution'])
                    # find the ray tracing solution that produces the largest amplitude
                    max_amps = np.argmax(np.argmax(As[:, :], axis=-1), axis=-1)
                    # advanced indexing: selects the ray tracing solution per event with the highest amplitude
                    triggered = triggered & (np.array(fin[f'station_{station:d}/ray_tracing_reflection'])[..., max_amps, 0][:, 0] == values['n_reflections'])

            triggered = remove_duplicate_triggers(triggered, fin['event_group_ids'])
            Veff = volume_proj_area * np.sum(weights[triggered]) / n_events
            Vefferror = 0
            if(np.sum(weights[triggered]) > 0):
                Vefferror = Veff / np.sum(weights[triggered]) ** 0.5
            FC_low, FC_high = FC_limits(np.sum(weights[triggered]))
            Veff_low = volume_proj_area * FC_low / n_events
            Veff_high = volume_proj_area * FC_high / n_events

            if('efficiency' in values.keys() and Veff > 0):
                get_efficiency = values['efficiency']['func']
                channel_ids = values['efficiency']['channel_ids']
                gids = np.array(fin['event_group_ids'])
                ugids = np.unique(np.array(fin['event_group_ids']))

                # calculate the group event ids that triggered
                ugids_triggered_index = []
                ugids_triggered = []
                for i_ugid, ugid in enumerate(ugids):
                    mask = ugid == gids
                    if(np.any(triggered[mask])):
                        ugids_triggered_index.append(i_ugid)
                        ugids_triggered.append(ugid)
                ugids_triggered = np.array(ugids_triggered)
                ugids_triggered_index = np.array(ugids_triggered_index)

                n_unique_gids = len(ugids_triggered)
                sorter = np.argsort(ugids_triggered)
                max_amplitudes = np.zeros(n_unique_gids)
                for key in fin.keys():
                    if(key.startswith("station_")):
                        if('event_group_ids' not in fin[key]):
                            continue  # the station might have no triggers
                        sgids = np.array(fin[key]['event_group_ids'])
                        usgids = np.unique(sgids)
                        usgids, comm1, comm2 = np.intersect1d(usgids, ugids_triggered, assume_unique=True, return_indices=True)  # select only the gids that triggered
                        common_mask = np.isin(sgids, usgids)
                        sgids = sgids[common_mask]  # also reduce gids array to the event groups that triggered
                        if(len(usgids) == 0):  # skip stations that don't have any trigger for this trigger combination
                            continue
                        usgids_index = sorter[np.searchsorted(ugids_triggered, usgids, sorter=sorter)]
                        # each station might have multiple triggeres per event group id. We need to select the one
                        # event with the largest amplitude. Let's first check if one event group created more than one event
                        max_amps_per_event_channel = np.nan_to_num(np.array(fin[key]['maximum_amplitudes_envelope'])[common_mask])
                        max_amps_per_event = np.amax(max_amps_per_event_channel[:, channel_ids], axis=1)  # select the maximum amplitude of all considered channels
                        if(len(sgids) != len(usgids)):
                            # at least one event group created more than one event. Let's calculate it the slow but correct way
                            for sgid in np.unique(sgids):  # loop over all event groups which triggered this station
                                if(sgid not in usgids):
                                    continue
                                mask_gid = sgid == sgids  # select all event that are part of this event group
                                index = np.squeeze(np.argwhere(ugids_triggered == sgid))
                                max_amplitudes[index] = max(max_amplitudes[index], max_amps_per_event[mask_gid].max())
                        else:
                            max_amplitudes[usgids_index] = np.maximum(max_amplitudes[usgids_index], max_amps_per_event)
                if('scale' in values['efficiency']):
                    max_amplitudes *= values['efficiency']['scale']
                if("Vrms" in values['efficiency']):
                    Vrms = values['efficiency']['Vrms']
                e = get_efficiency(max_amplitudes / Vrms)  # we calculated the maximum amplitudes for all gids, now we select only those that triggered
                Veff = volume_proj_area * np.sum(weights[triggered] * e) / n_events
                Vefferror = 0
                if(np.sum(weights[triggered]) > 0):
                    Vefferror = Veff / np.sum(weights[triggered] * e) ** 0.5
                FC_low, FC_high = FC_limits(np.sum(weights[triggered] * e))
                Veff_low = volume_proj_area * FC_low / n_events
                Veff_high = volume_proj_area * FC_high / n_events

            out[veff_aeff][trigger_name] = [Veff, Vefferror, np.sum(weights[triggered]), Veff_low, Veff_high]
    return out


def tmp(args):
    return get_Veff_Aeff_single(*args)


def get_Veff_Aeff(folder,
             trigger_combinations={},
             station=101,
             veff_aeff="veff",
             n_cores=1):
    """
    calculates the effective volume or effective area from surface muons from NuRadioMC hdf5 files

    the effective volume is NOT normalized to a water equivalent. It is also NOT multiplied with the solid angle (typically 4pi).

    Parameters
    ----------
    folder: string
        folder conaining the hdf5 files, one per energy OR filename
    trigger_combinations: dict, optional
        keys are the names of triggers to calculate. Values are dicts again:
            * 'triggers': list of strings
                name of individual triggers that are combined with an OR
            the following additional options are optional
            * 'efficiency': dict
                allows to apply an (analysis) efficiency cut for calculating effective volumes
                * 'func': function
                    a function that paramaterized the efficiency as a function of SNR (=Vmax/Vrms)
                * 'channel_ids': array on ints
                    the channels for which the maximum signal amplitude should be determined
                * 'scale': float
                    rescaling of the efficiency curve by SNR' = SNR * scale
            * 'n_reflections': int
                the number of bottom reflections of the ray tracing solution that likely triggered
                assuming that the solution with the shortest travel time caused the trigger, only considering channel 0

    station: int
        the station that should be considered
    veff_aeff: string
        specifiy if the effective volume or the effective area for surface muons is calculated
        can be 
        * "veff" (default)
        * "aeff_surface_muons"
        
    n_cores: int
        the number of cores to use

    Returns
    ----------
    list of dictionary. Each file is one entry. The dictionary keys store all relevant properties
    """
    trigger_combinations = copy.copy(trigger_combinations)
    trigger_names = None
    trigger_names_dict = {}
    prev_deposited = None
    deposited = False

    if(os.path.isfile(folder)):
        filenames = [folder]
    else:
        if(len(glob.glob(os.path.join(folder, '*.hdf5'))) == 0):
            raise FileNotFoundError(f"couldnt find any hdf5 file in folder {folder}")
        filenames = sorted(glob.glob(os.path.join(folder, '*.hdf5')))
    for iF, filename in enumerate(filenames):
        logger.info(f"reading {filename}")
        fin = h5py.File(filename, 'r')
        if 'deposited' in fin.attrs:
            deposited = fin.attrs['deposited']
            if prev_deposited is None:
                prev_deposited = deposited
            elif prev_deposited != deposited:
                raise AttributeError("The deposited parameter is not consistent among the input files!")

        if('trigger_names' in fin.attrs):
            trigger_names = fin.attrs['trigger_names']
            if(len(trigger_names) > 0):
                for iT, trigger_name in enumerate(trigger_names):
                    trigger_names_dict[trigger_name] = iT
                break

    trigger_combinations['all_triggers'] = {'triggers': trigger_names}
    logger.info(f"Trigger names:  {trigger_names}")
    for key in trigger_combinations:
        i = -1
        for value in trigger_combinations[key]['triggers']:
            i += 1
            if value not in trigger_names:
                logger.warning(f"trigger {value} not available, removing this trigger from the trigger combination {key}")
                trigger_combinations[key]['triggers'].pop(i)
                i -= 1
    from multiprocessing import Pool
    logger.warning(f"running {len(filenames)} jobs on {n_cores} cores")

    args = []
    for f in filenames:
        args.append([f, trigger_names, trigger_names_dict, trigger_combinations, deposited, station, veff_aeff])
    with Pool(n_cores) as p:
        output = p.map(tmp, args)
        print("output")
        print(output)
        return output


def get_Veff_Aeff_array(data):
    """
    calculates a multi dimensional array of effective volume or effective area from surface muons calculations for fast slicing

    the array dimensions are (energy, zenith bin, triggername, 5) where the
    last tuple is the effective volume, its uncertainty, the weighted sum of triggered events, lower 68% uncertainty, upper 68% uncertainty

    Parameters
    -----------
    data: dict
        the result of the `get_Veff` function

    Returns
    --------
     * (n_energy, n_zenith_bins, n_triggernames, 5) dimensional array of floats
     * array of unique mean energies (the mean is calculated in the logarithm of the energy)
     * array of unique lower bin edges of energies
     * array of unique upper bin edges of energies
     * array of unique zenith bins
     * array of unique trigger names


    Examples
    ---------

    To plot the full sky effective volume for 'all_triggers' do

    ```
    output, uenergies, uzenith_bins, utrigger_names, zenith_weights = get_Veff_array(data)


    fig, ax = plt.subplots(1, 1)
    tname = "all_triggers"
    Veff = np.average(output[:,:,get_index(tname, utrigger_names),0], axis=1, weights=zenith_weights)
    Vefferror = Veff / np.sum(output[:,:,get_index(tname, utrigger_names),2], axis=1)**0.5
    ax.errorbar(uenergies/units.eV, Veff/units.km**3 * 4 * np.pi, yerr=Vefferror/units.km**3 * 4 * np.pi, fmt='-o', label=tname)

    ax.legend()
    ax.semilogy(True)
    ax.semilogx(True)
    fig.tight_layout()
    plt.show()
    ```


    To plot the effective volume for different declination bands do

    ```
    fig, ax = plt.subplots(1, 1)
    tname = "LPDA_2of4_100Hz"
    iZ = 9
    Veff = output[:,iZ,get_index(tname, utrigger_names)]
    ax.errorbar(uenergies/units.eV, Veff[:,0]/units.km**3, yerr=Veff[:,1]/units.km**3,
                label=f"zenith bin {uzenith_bins[iZ][0]/units.deg:.0f} - {uzenith_bins[iZ][1]/units.deg:.0f}")

    iZ = 8
    Veff = output[:,iZ,get_index(tname, utrigger_names)]
    ax.errorbar(uenergies/units.eV, Veff[:,0]/units.km**3, yerr=Veff[:,1]/units.km**3,
                label=f"zenith bin {uzenith_bins[iZ][0]/units.deg:.0f} - {uzenith_bins[iZ][1]/units.deg:.0f}")
    iZ = 7
    Veff = output[:,iZ,get_index(tname, utrigger_names)]
    ax.errorbar(uenergies/units.eV, Veff[:,0]/units.km**3, yerr=Veff[:,1]/units.km**3,
                label=f"zenith bin {uzenith_bins[iZ][0]/units.deg:.0f} - {uzenith_bins[iZ][1]/units.deg:.0f}")
    iZ = 10
    Veff = output[:,iZ,get_index(tname, utrigger_names)]
    ax.errorbar(uenergies/units.eV, Veff[:,0]/units.km**3, yerr=Veff[:,1]/units.km**3,
                label=f"zenith bin {uzenith_bins[iZ][0]/units.deg:.0f} - {uzenith_bins[iZ][1]/units.deg:.0f}")


    ax.legend()
    ax.semilogy(True)
    ax.semilogx(True)
    fig.tight_layout()
    plt.show()
    ```

    """
    energies = []
    energies_min = []
    energies_max = []
    zenith_bins = []
    trigger_names = []
    veff_aeff = None
    for d in data:
        if(veff_aeff is None):
            if "veff" in d:
                veff_aeff = "veff"
                print(f"data contains effective volume")
            elif "aeff_surface_muons" in d:
                veff_aeff = "aeff_surface_muons"
                print(f"data contains effective area for surface muons")
            else:
                print(f"dictionary does neither contain key `veff` nor `aeff_surface_muons`")
                raise AttributeError(f"dictionary does neither contain key `veff` nor `aeff_surface_muons`")
        energies.append(d['energy'])
        energies_min.append(d['energy_min'])
        energies_max.append(d['energy_max'])
        zenith_bins.append([d['thetamin'], d['thetamax']])
        for triggername in d[veff_aeff]:
            trigger_names.append(triggername)

    energies = np.array(energies)
    energies_min = np.array(energies_min)
    energies_max = np.array(energies_max)
    zenith_bins = np.array(zenith_bins)
    trigger_names = np.array(trigger_names)
    uenergies = np.unique(energies)
    uenergies_min = np.unique(energies_min)
    uenergies_max = np.unique(energies_max)
    uzenith_bins = np.unique(zenith_bins, axis=0)
    utrigger_names = np.unique(trigger_names)
    output = np.zeros((len(uenergies), len(uzenith_bins), len(utrigger_names), 5))
    logger.debug(f"unique energies {uenergies}")
    logger.debug(f"unique zenith angle bins {uzenith_bins/units.deg}")
    logger.debug(f"unique energies {utrigger_names}")

    for d in data:
        iE = np.squeeze(np.argwhere(d['energy'] == uenergies))
        iT = np.squeeze(np.argwhere([d['thetamin'], d['thetamax']] == uzenith_bins))[0][0]
        for triggername, Veff in d[veff_aeff].items():
            iTrig = np.squeeze(np.argwhere(triggername == utrigger_names))
            output[iE, iT, iTrig] = Veff

    for d in data:
        iE = np.squeeze(np.argwhere(d['energy'] == uenergies))
        iT = np.squeeze(np.argwhere([d['thetamin'], d['thetamax']] == uzenith_bins))[0][0]

    return output, uenergies, uenergies_min, uenergies_max, uzenith_bins, utrigger_names


def get_index(value, array):
    return np.squeeze(np.argwhere(value == array))


def export(filename, data, trigger_names=None, export_format='yaml'):
    """
    export effective volumes (or effective areas) into a human readable JSON or YAML file

    Parameters
    ----------
    filename: string
        the output filename of the JSON file
    data: array
        the output of the `getVeff` function
    trigger_names: list of strings (optional, default None)
        save only specific trigger names, if None all triggers are exported
    export_format: string (default "yaml")
        specify output format, choose
        * "yaml"
        * "json"
    """
    output = []
    for i in range(len(data)):
        tmp = {}
        for key in data[i]:
            if (key not in  ['veffs', 'aeff_surface_muons']):
                if isinstance(data[i][key], np.generic):
                    tmp[key] = data[i][key].item()
                else:
                    tmp[key] = data[i][key]
        for key in ["veffs", "aeff_surface_muons"]:
            if(key in data[i]):
                tmp[key] = {}
                for trigger_name in data[i][key]:
                    if(trigger_names is None or trigger_name in trigger_names):
                        logger.info(trigger_name)
                        tmp[key][trigger_name] = []
                        for value in data[i][key][trigger_name]:
                            tmp[key][trigger_name].append(float(value))
        output.append(tmp)

    with open(filename, 'w') as fout:
        if(export_format == 'yaml'):
            import yaml
            yaml.dump(output, fout)
        elif(export_format == 'json'):
            json.dump(output, fout, sort_keys=True, indent=4)
