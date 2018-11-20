# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function
import numpy as np
from NuRadioMC.utilities import units, fft
import logging
logger = logging.getLogger("SignalGen.parametrizations")


def set_log_level(level):
    logger.setLevel(level)


"""

Analytic parametrizations of the radio pulse produced by an in-ice particle shower.

Generic functions to provide the frequency spectrum and the pulse in the time domain
are defined. All models/parametrizations should be added to each of these functions,
such that different parametrizations can be exchanged by just modifying the 'model'
argument of the respective function.

"""


def get_parametrizations():
    """ returns a list of all implemented parametrizations """
    return ['ZHS1992', 'Alvarez2000', 'Alvarez2011']


def get_time_trace(energy, theta, N, dt, is_em_shower, n_index, R, model):
    """
    returns the Askaryan pulse in the time domain of the eTheta component

    We implement only the time-domain solution and obtain the frequency spectrum
    via FFT (with the standard normalization of NuRadioMC). This approach assures
    that the units are interpreted correctly. In the time domain, the amplitudes
    are well defined and not details about fourier transform normalizations needs
    to be known by the user.

    Parameters
    ----------
    energy : float
        energy of the shower
    theta: float
        viewangle: angle between shower axis (neutrino direction) and the line
        of sight between interaction and detector
    N : int
        number of samples in the time domain
    dt: float
        time bin width, i.e. the inverse of the sampling rate
    is_em_shower: bool
        true if EM shower, false otherwise
    n_index: float
        index of refraction at interaction vertex
    R: float
        distance from vertex to observer
    model: string
        specifies the signal model
        * ZHS1992: the original ZHS parametrization from E. Zas, F. Halzen, and T. Stanev, Phys. Rev. D 45, 362 (1992), doi:10.1103/PhysRevD.45.362, this parametrization does not contain any phase information
        * Alvarez2000: what is in shelfmc
        * Alvarez2011: parametrization based on ZHS from Jaime Alvarez-Muñiz, Andrés Romero-Wolf, and Enrique Zas Phys. Rev. D 84, 103003, doi:10.1103/PhysRevD.84.103003. The model is implemented in pyrex and here only a wrapper around the pyrex code is implemented
        * Hanson2017: analytic model from J. Hanson, A. Connolly Astroparticle Physics 91 (2017) 75-89

    Returns
    -------
    spectrum: array
        the complex amplitudes for the given frequencies

    """
    if(model == 'ZHS1992'):
        """ Parametrization from E. Zas, F. Halzen, and T. Stanev, Phys. Rev. D 45, 362 (1992)."""
        freqs = np.fft.rfftfreq(N, dt)
        vv0 = freqs / (0.5 * units.GHz)
        cherenkov_angle = np.arccos(1. / n_index)
        domega = (theta - cherenkov_angle)
        tmp = np.exp(+0.5j * np.pi)  # set phases to 90deg
        tmp *= 1.1e-7 * energy / units.TeV * vv0 * 1. / \
            (1 + 0.4 * (vv0) ** 2) * np.exp(-0.5 * (domega / (2.4 * units.deg / vv0)) ** 2) * \
            units.V / units.m / (R / units.m) / units.MHz
        # the factor 0.5 is introduced to compensate the unusual fourier transform normalization used in the ZHS code
        trace = 0.5 * np.fft.irfft(tmp) / dt
        trace = np.roll(trace, int(2 * units.ns / dt))
        return trace

    elif(model == 'Alvarez2012'):
        from pyrex.signals import AskaryanSignal
        from pyrex.ice_model import IceModel
        from pyrex.particle import Particle
        tt = np.arange(0, N * dt, dt)
        ice = IceModel()
        p = Particle(particle_id="nu_e", # irrelevant
                     vertex=(0, 0, ice.depth_with_index(n_index)),
                     direction=(0, 0, -1), # irrelevant
                     energy=energy/units.GeV)
        p.interaction.em_frac = int(is_em_shower)
        p.interaction.had_frac = 1 - p.interaction.em_frac
        ask = AskaryanSignal(times=tt / units.s, 
                            particle=p,
                            viewing_angle=theta,
                            viewing_distance=R,
                            ice_model=ice,
                            t0=20 * units.ns / units.s)
        
        trace = ask.values * units.V / units.m
        return trace

    elif(model == 'Alvarez2000'):
        freqs = np.fft.rfftfreq(N, dt)[1:]  # exclude zero frequency
        cherenkov_angle = np.arccos(1. / n_index)

        Elpm = 2e15 * units.eV
        dThetaEM = 2.7 * units.deg * 500 * units.MHz / freqs * (Elpm / (0.14 * energy + Elpm)) ** 0.3
#         logger.debug("dThetaEM = {}".format(dThetaEM))

        epsilon = np.log10(energy / units.TeV)
        dThetaHad = 0
        if (epsilon >= 0 and epsilon <= 2):
            dThetaHad = 500 * units.MHz / freqs * (2.07 - 0.33 * epsilon + 7.5e-2 * epsilon ** 2) * units.deg
        elif (epsilon > 2 and epsilon <= 5):
            dThetaHad = 500 * units.MHz / freqs * (1.74 - 1.21e-2 * epsilon) * units.deg
        elif(epsilon > 5 and epsilon <= 7):
            dThetaHad = 500 * units.MHz / freqs * (4.23 - 0.785 * epsilon + 5.5e-2 * epsilon ** 2) * units.deg
        elif(epsilon > 7):
            dThetaHad = 500 * units.MHz / freqs * (4.23 - 0.785 * 7 + 5.5e-2 * 7 ** 2) * \
                (1 + (epsilon - 7) * 0.075) * units.deg

        f0 = 1.15 * units.GHz
        E = 2.53e-7 * energy / units.TeV * freqs / f0 / (1 + (freqs / f0) ** 1.44)
        E *= units.V / units.m / units.MHz
        E *= np.sin(theta) / np.sin(cherenkov_angle)

        tmp = np.zeros(len(freqs) + 1)
        if(is_em_shower):
            tmp[1:] = E * np.exp(-np.log(2) * ((theta - cherenkov_angle) / dThetaEM) ** 2) / R
        else:
            if(np.any(dThetaHad != 0)):
                tmp[1:] = E * np.exp(-np.log(2) * ((theta - cherenkov_angle) / dThetaHad) ** 2) / R
            else:
                pass
                # energy is below a TeV, setting Askaryan pulse to zero

        tmp *= 0.5  # the factor 0.5 is introduced to compensate the unusual fourier transform normalization used in the ZHS code

#         df = np.mean(freqs[1:] - freqs[:-1])
        trace = np.fft.irfft(tmp * np.exp(0.5j * np.pi)) / dt  # set phases to 90deg
        trace = np.roll(trace, int(50 * units.ns / dt))
        return trace

    elif(model == 'Hanson2017'):
        from NuRadioMC.SignalGen.RalstonBuniy import askaryan_module
        return askaryan_module.get_time_trace(energy, theta, N, dt, is_em_shower, n_index, R)
    
    elif(model == 'spherical'):
        amplitude = 1. * energy / R
        trace = np.zeros(N)
        trace[N//2] = amplitude
        return trace
    
    else:
        raise NotImplementedError("model {} unknown".format(model))


def get_frequency_spectrum(energy, theta, N, dt, is_em_shower, n_index, R, model):
    """
    returns the complex amplitudes of the frequency spectrum of the neutrino radio signal

    Parameters
    ----------
    energy : float
        energy of the shower
    theta: float
        viewangle: angle between shower axis (neutrino direction) and the line
        of sight between interaction and detector
    N : int
        number of samples in the time domain
    dt: float
        time bin width, i.e. the inverse of the sampling rate
    is_em_shower: bool
        true if EM shower, false otherwise
    n_index: float
        index of refraction at interaction vertex
    R: float
        distance from vertex to observer
    model: string
        specifies the signal model
        * ZHS1992: the original ZHS parametrization from E. Zas, F. Halzen, and T. Stanev, Phys. Rev. D 45, 362 (1992), doi:10.1103/PhysRevD.45.362, this parametrization does not contain any phase information
        * Alvarez2000: what is in shelfmc
        * Alvarez2011: parametrization based on ZHS from Jaime Alvarez-Muñiz, Andrés Romero-Wolf, and Enrique Zas Phys. Rev. D 84, 103003, doi:10.1103/PhysRevD.84.103003. The model is implemented in pyrex and here only a wrapper around the pyrex code is implemented

    Returns
    -------
    spectrum: array
        the complex amplitudes for the given frequencies

    """
    return fft.time2freq(get_time_trace(energy, theta, N, dt, is_em_shower, n_index, R, model))