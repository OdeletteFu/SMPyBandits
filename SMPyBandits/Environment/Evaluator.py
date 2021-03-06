# -*- coding: utf-8 -*-
""" Evaluator class to wrap and run the simulations.
Lots of plotting methods, to have various visualizations.
"""
from __future__ import division, print_function  # Python 2 compatibility

__author__ = "Lilian Besson"
__version__ = "0.9"

# Generic imports
from copy import deepcopy
import random
import time
# Scientific imports
import numpy as np
import matplotlib.pyplot as plt

import inspect
def _nbOfArgs(function):
    try: return len(inspect.signature(functions).parameters)
    except NameError: return len(inspect.getargspec(function).args)

try:
    # Local imports, libraries
    from .usejoblib import USE_JOBLIB, Parallel, delayed
    from .usetqdm import USE_TQDM, tqdm
    # Local imports, tools and config
    from .plotsettings import BBOX_INCHES, signature, maximizeWindow, palette, makemarkers, add_percent_formatter, legend, show_and_save, nrows_ncols, addTextForWorstCases
    from .sortedDistance import weightedDistance, manhattan, kendalltau, spearmanr, gestalt, meanDistance, sortedDistance
    # Local imports, objects and functions
    from .MAB import MAB, MarkovianMAB, DynamicMAB, IncreasingMAB
    from .Result import Result
    from .memory_consumption import getCurrentMemory, sizeof_fmt
except ImportError:
    # Local imports, libraries
    from usejoblib import USE_JOBLIB, Parallel, delayed
    from usetqdm import USE_TQDM, tqdm
    # Local imports, tools and config
    from plotsettings import BBOX_INCHES, signature, maximizeWindow, palette, makemarkers, add_percent_formatter, legend, show_and_save, nrows_ncols, addTextForWorstCases
    from sortedDistance import weightedDistance, manhattan, kendalltau, spearmanr, gestalt, meanDistance, sortedDistance
    # Local imports, objects and functions
    from MAB import MAB, MarkovianMAB, DynamicMAB, IncreasingMAB
    from Result import Result
    from memory_consumption import getCurrentMemory, sizeof_fmt


REPETITIONS = 1    #: Default nb of repetitions
DELTA_T_PLOT = 50  #: Default sampling rate for plotting

plot_lowerbound = True  #: Default is to plot the lower-bound

# Parameters for the random events
random_shuffle = False
random_invert = False
nb_random_events = 5  #: Default nb of random events

# Flag for experimental aspects
STORE_ALL_REWARDS = True       #: Store all rewards?
STORE_ALL_REWARDS = False      #: Store all rewards?
STORE_REWARDS_SQUARED = True   #: Store rewards squared?
STORE_REWARDS_SQUARED = False  #: Store rewards squared?
MORE_ACCURATE = False          #: Use the count of selections instead of rewards for a more accurate mean/std reward measure.
MORE_ACCURATE = True           #: Use the count of selections instead of rewards for a more accurate mean/std reward measure.
FINAL_RANKS_ON_AVERAGE = True
USE_JOBLIB_FOR_POLICIES = False


class Evaluator(object):
    """ Evaluator class to run the simulations."""

    def __init__(self, configuration,
                 finalRanksOnAverage=FINAL_RANKS_ON_AVERAGE, averageOn=5e-3,
                 useJoblibForPolicies=USE_JOBLIB_FOR_POLICIES,
                 moreAccurate=MORE_ACCURATE):
        self.cfg = configuration  #: Configuration dictionnary
        # Attributes
        self.nbPolicies = len(self.cfg['policies'])  #: Number of policies
        print("Number of policies in this comparison:", self.nbPolicies)
        self.horizon = self.cfg['horizon']  #: Horizon (number of time steps)
        print("Time horizon:", self.horizon)
        self.repetitions = self.cfg.get('repetitions', REPETITIONS)  #: Number of repetitions
        print("Number of repetitions:", self.repetitions)
        self.delta_t_plot = 1 if self.horizon <= 10000 else self.cfg.get('delta_t_plot', DELTA_T_PLOT)  #: Sampling rate for plotting
        print("Sampling rate for plotting, delta_t_plot:", self.delta_t_plot)
        print("Number of jobs for parallelization:", self.cfg['n_jobs'])
        # Parameters for the random events
        self.random_shuffle = self.cfg.get('random_shuffle', random_shuffle)  #: Random shuffling of arms?
        self.random_invert = self.cfg.get('random_invert', random_invert)  #: Random inversion of arms?
        self.nb_random_events = self.cfg.get('nb_random_events', nb_random_events)  #: How many random events?
        self.plot_lowerbound = self.cfg.get('plot_lowerbound', plot_lowerbound)  #: Should we plot the lower-bound?
        self.signature = signature
        if self.nb_random_events > 0:
            if self.random_shuffle:
                self.signature = (r", $\Upsilon={}$ random arms shuffling".format(self.nb_random_events - 1)) + self.signature
            elif self.random_invert:
                self.signature = (r", $\Upsilon={}$ arms inversion".format(self.nb_random_events - 1)) + self.signature
        # Flags
        self.moreAccurate = moreAccurate  #: Use the count of selections instead of rewards for a more accurate mean/std reward measure.
        self.finalRanksOnAverage = finalRanksOnAverage  #: Final display of ranks are done on average rewards?
        self.averageOn = averageOn  #: How many last steps for final rank average rewards
        self.useJoblibForPolicies = useJoblibForPolicies  #: Use joblib to parallelize for loop on policies (useless)
        self.useJoblib = USE_JOBLIB and self.cfg['n_jobs'] != 1  #: Use joblib to parallelize for loop on repetitions (useful)
        self.cache_rewards = self.cfg.get('cache_rewards', False)  #: Should we cache and precompute rewards
        self.showplot = self.cfg.get('showplot', True)  #: Show the plot (interactive display or not)

        self.change_labels = self.cfg.get('change_labels', {})  #: Possibly empty dictionary to map 'policyId' to new labels (overwrite their name).
        self.append_labels = self.cfg.get('append_labels', {})  #: Possibly empty dictionary to map 'policyId' to new labels (by appending the result from 'append_labels').

        # Internal object memory
        self.envs = []  #: List of environments
        self.policies = []  #: List of policies
        self.__initEnvironments__()

        # Internal vectorial memory
        self.rewards = np.zeros((self.nbPolicies, len(self.envs), self.horizon))  #: For each env, history of rewards, ie accumulated rewards
        self.lastCumRewards = np.zeros((self.nbPolicies, len(self.envs), self.repetitions))  #: For each env, last accumulated rewards, to compute variance and histogram of whole regret R_T
        self.minCumRewards = np.inf + np.zeros((self.nbPolicies, len(self.envs), self.horizon))  #: For each env, history of minimum of rewards, to compute amplitude (+- STD)
        self.maxCumRewards = -np.inf + np.zeros((self.nbPolicies, len(self.envs), self.horizon))  #: For each env, history of maximum of rewards, to compute amplitude (+- STD)

        if STORE_REWARDS_SQUARED:
            self.rewardsSquared = np.zeros((self.nbPolicies, len(self.envs), self.horizon))  #: For each env, history of rewards squared
        if STORE_ALL_REWARDS:
            self.allRewards = np.zeros((self.nbPolicies, len(self.envs), self.horizon, self.repetitions))  #: For each env, full history of rewards

        self.bestArmPulls = dict()  #: For each env, keep the history of best arm pulls
        self.pulls = dict()  #: For each env, keep cumulative counts of all arm pulls
        if self.moreAccurate: self.allPulls = dict()  #: For each env, keep cumulative counts of all arm pulls
        self.lastPulls = dict()  #: For each env, keep cumulative counts of all arm pulls
        self.runningTimes = dict()  #: For each env, keep the history of running times
        self.memoryConsumption = dict()  #: For each env, keep the history of running times
        # XXX: WARNING no memorized vectors should have dimension duration * repetitions, that explodes the RAM consumption!
        for envId in range(len(self.envs)):
            self.bestArmPulls[envId] = np.zeros((self.nbPolicies, self.horizon), dtype=np.int32)
            self.pulls[envId] = np.zeros((self.nbPolicies, self.envs[envId].nbArms), dtype=np.int32)
            if self.moreAccurate: self.allPulls[envId] = np.zeros((self.nbPolicies, self.envs[envId].nbArms, self.horizon), dtype=np.int32)
            self.lastPulls[envId] = np.zeros((self.nbPolicies, self.envs[envId].nbArms, self.repetitions), dtype=np.int32)
            self.runningTimes[envId] = np.zeros((self.nbPolicies, self.repetitions))
            self.memoryConsumption[envId] = np.zeros((self.nbPolicies, self.repetitions))
        print("Number of environments to try:", len(self.envs))
        # To speed up plotting
        self._times = np.arange(1, 1 + self.horizon)

    # --- Init methods

    def __initEnvironments__(self):
        """ Create environments."""
        for configuration_arms in self.cfg['environment']:
            if isinstance(configuration_arms, dict) \
               and "arm_type" in configuration_arms and "params" in configuration_arms \
               and "function" in configuration_arms["params"] and "args" in configuration_arms["params"]:
                self.envs.append(DynamicMAB(configuration_arms))
            elif isinstance(configuration_arms, dict) \
               and "arm_type" in configuration_arms and configuration_arms["arm_type"] == "Markovian" \
               and "params" in configuration_arms \
               and "transitions" in configuration_arms["params"]:
                self.envs.append(MarkovianMAB(configuration_arms))
            elif isinstance(configuration_arms, dict) \
               and "arm_type" in configuration_arms and "params" in configuration_arms \
               and "change_lower_amplitude" in configuration_arms:
                self.envs.append(IncreasingMAB(configuration_arms))
                IncreasingMAB
            else:
                self.envs.append(MAB(configuration_arms))

    def __initPolicies__(self, env):
        """ Create or initialize policies."""
        for policyId, policy in enumerate(self.cfg['policies']):
            print("- Adding policy #{} = {} ...".format(policyId + 1, policy))  # DEBUG
            if isinstance(policy, dict):
                print("  Creating this policy from a dictionnary 'self.cfg['policies'][{}]' = {} ...".format(policyId, policy))  # DEBUG
                self.policies.append(policy['archtype'](env.nbArms, **policy['params']))
            else:
                print("  Using this already created policy 'self.cfg['policies'][{}]' = {} ...".format(policyId, policy))  # DEBUG
                self.policies.append(policy)
        for policyId in range(self.nbPolicies):
            self.policies[policyId].__cachedstr__ = str(self.policies[policyId])
            if policyId in self.append_labels:
                self.policies[policyId].__cachedstr__ += self.append_labels[policyId]
            if policyId in self.change_labels:
                self.policies[policyId].__cachedstr__ = self.append_labels[policyId]


    # --- Start computation

    def compute_cache_rewards(self, arms):
        """ Compute only once the rewards, then launch the experiments with the same matrix (r_{k,t})."""
        rewards = np.zeros((len(arms), self.repetitions, self.horizon))
        print("\n===> Pre-computing the rewards ... Of shape {} ...\n    In order for all simulated algorithms to face the same random rewards (robust comparison of A1,..,An vs Aggr(A1,..,An)) ...\n".format(np.shape(rewards)))  # DEBUG
        for armId, arm in tqdm(enumerate(arms), desc="Arms"):
            if hasattr(arm, 'draw_nparray'):  # XXX Use this method to speed up computation
                rewards[armId] = arm.draw_nparray((self.repetitions, self.horizon))
            else:  # Slower
                for repeatId in tqdm(range(self.repetitions), desc="Repetitions"):
                    for t in tqdm(range(self.horizon), desc="Time steps"):
                        rewards[armId, repeatId, t] = arm.draw(t)
        return rewards

    def startAllEnv(self):
        """Simulate all envs."""
        for envId, env in enumerate(self.envs):
            self.startOneEnv(envId, env)

    def startOneEnv(self, envId, env):
        """Simulate that env."""
        print("\n\nEvaluating environment:", repr(env))
        self.policies = []
        self.__initPolicies__(env)
        # Precompute rewards
        if self.cache_rewards:
            allrewards = self.compute_cache_rewards(env.arms)
        else:
            allrewards = None

        def store(r, policyId, repeatId):
            """ Store the result of the #repeatId experiment, for the #policyId policy."""
            self.rewards[policyId, envId, :] += r.rewards
            self.lastCumRewards[policyId, envId, repeatId] = np.sum(r.rewards)
            if hasattr(self, 'rewardsSquared'):
                self.rewardsSquared[policyId, envId, :] += (r.rewards ** 2)
            if hasattr(self, 'allRewards'):
                self.allRewards[policyId, envId, :, repeatId] = r.rewards
            if hasattr(self, 'minCumRewards'):
                self.minCumRewards[policyId, envId, :] = np.minimum(self.minCumRewards[policyId, envId, :], np.cumsum(r.rewards)) if repeatId > 1 else np.cumsum(r.rewards)
            if hasattr(self, 'maxCumRewards'):
                self.maxCumRewards[policyId, envId, :] = np.maximum(self.maxCumRewards[policyId, envId, :], np.cumsum(r.rewards)) if repeatId > 1 else np.cumsum(r.rewards)
            self.bestArmPulls[envId][policyId, :] += np.cumsum(np.in1d(r.choices, r.indexes_bestarm))
            self.pulls[envId][policyId, :] += r.pulls
            if self.moreAccurate: self.allPulls[envId][policyId, :, :] += np.array([1 * (r.choices == armId) for armId in range(env.nbArms)])  # XXX consumes a lot of zeros but it is not so costly
            self.lastPulls[envId][policyId, :, repeatId] = r.pulls
            self.runningTimes[envId][policyId, repeatId] = r.running_time
            self.memoryConsumption[envId][policyId, repeatId] = r.memory_consumption

        # Start for all policies
        for policyId, policy in enumerate(self.policies):
            print("\n\n\n- Evaluating policy #{}/{}: {} ...".format(policyId + 1, self.nbPolicies, policy))
            if self.useJoblib:
                seeds = np.random.randint(low=0, high=100 * self.repetitions, size=self.repetitions)
                repeatIdout = 0
                for r in Parallel(n_jobs=self.cfg['n_jobs'], verbose=self.cfg['verbosity'])(
                    delayed(delayed_play)(env, policy, self.horizon, random_shuffle=self.random_shuffle, random_invert=self.random_invert, nb_random_events=self.nb_random_events, allrewards=allrewards, seed=seeds[repeatId], repeatId=repeatId, useJoblib=self.useJoblib)
                    for repeatId in tqdm(range(self.repetitions), desc="Repeat||")
                ):
                    store(r, policyId, repeatIdout)
                    repeatIdout += 1
            else:
                for repeatId in tqdm(range(self.repetitions), desc="Repeat"):
                    r = delayed_play(env, policy, self.horizon, random_shuffle=self.random_shuffle, random_invert=self.random_invert, nb_random_events=self.nb_random_events, allrewards=allrewards, repeatId=repeatId, useJoblib=self.useJoblib)
                    store(r, policyId, repeatId)

    # --- Save to disk methods

    def saveondisk(self, filepath="saveondisk_Evaluator.hdf5"):
        """ Save the content of the internal data to into a HDF5 file on the disk.

        - See http://docs.h5py.org/en/stable/quick.html if needed.
        """
        import h5py
        # 1. create the h5py file
        h5file = h5py.File(filepath, "w")

        # 2. store main attributes and all other attributes, if they exist
        for name_of_attr in [
                "horizon", "repetitions", "nbPolicies",
                "delta_t_plot", "random_shuffle", "random_invert", "nb_random_events", "plot_lowerbound", "signature", "nb_random_events", "moreAccurate", "finalRanksOnAverage", "averageOn", "useJoblibForPolicies", "useJoblib", "cache_rewards", "showplot", "change_labels", "append_labels"
            ]:
            if not hasattr(self, name_of_attr): continue
            value = getattr(self, name_of_attr)
            if isinstance(value, str): value = np.string_(value)
            try: h5file.attrs[name_of_attr] = value
            except (ValueError, TypeError):
                print("Error: when saving the Evaluator object to a HDF5 file, the attribute named {} (value {} of type {}) couldn't be saved. Skipping...".format(name_of_attr, value, type(value)))  # DEBUG

        # 3. store some arrays that are shared between envs?
        for name_of_dataset in ["rewards", "rewardsSquared", "allRewards"]:
            if not hasattr(self, name_of_dataset): continue
            data = getattr(self, name_of_dataset)
            try: h5file.create_dataset(name_of_dataset, data=data)
            except (ValueError, TypeError) as e:
                print("Error: when saving the Evaluator object to a HDF5 file, the dataset named {} (value of type {} and shape {} and dtype {}) couldn't be saved. Skipping...".format(name_of_dataset, type(data), data.shape, data.dtype))  # DEBUG
                print("Exception:\n", e)  # DEBUG

        # 4. for each environment
        h5file.attrs["number_of_envs"] = len(self.envs)
        for envId in range(len(self.envs)):
            # 4.a. create subgroup for this env
            sbgrp = h5file.create_group("env_{}".format(envId))
            # 4.b. store attribute of the MAB problem
            mab = self.envs[envId]
            for name_of_attr in ["isDynamic", "isMarkovian", "_sparsity", "means", "nbArms", "maxArm", "minArm"]:
                if not hasattr(mab, name_of_attr): continue
                value = getattr(mab, name_of_attr)
                if isinstance(value, str): value = np.string_(value)
                try: sbgrp.attrs[name_of_attr] = value
                except (ValueError, TypeError):
                    print("Error: when saving the Evaluator object to a HDF5 file, the attribute named {} (value {} of type {}) couldn't be saved. Skipping...".format(name_of_attr, value, type(value)))  # DEBUG
            # 4.c. store data for that env
            for name_of_dataset in ["allPulls", "lastPulls", "runningTimes", "memoryConsumption"]:
                if not ( hasattr(self, name_of_dataset) and envId in getattr(self, name_of_dataset) ): continue
                data = getattr(self, name_of_dataset)[envId]
                try: sbgrp.create_dataset(name_of_dataset, data=data)
                except (ValueError, TypeError) as e:
                    print("Error: when saving the Evaluator object to a HDF5 file, the dataset named {} (value of type {} and shape {} and dtype {}) couldn't be saved. Skipping...".format(name_of_dataset, type(data), data.shape, data.dtype))  # DEBUG
                    print("Exception:\n", e)  # DEBUG

            # 4.d. compute and store data for that env
            for methodName in ["getRunningTimes", "getMemoryConsumption", "getBestArmPulls", "getPulls", "getRewards", "getCumulatedRegret", "getLastRegrets", "getAverageRewards"]:
                if not hasattr(self, methodName): continue
                name_of_dataset = methodName.replace("get", "")
                name_of_dataset = name_of_dataset[0].lower() + name_of_dataset[1:]
                if name_of_dataset in sbgrp: name_of_dataset = methodName  # XXX be sure to not use twice the same name, e.g., for getRunningTimes and runningTimes
                method = getattr(self, methodName)
                if _nbOfArgs(method) > 2:
                    if isinstance(method(0, envId=envId), tuple):
                        data = np.array([method(policyId, envId=envId)[0] for policyId in range(len(self.policies))])
                    else:
                        data = np.array([method(policyId, envId=envId) for policyId in range(len(self.policies))])
                else:
                    if isinstance(method(envId), tuple):
                        data = method(envId)[0]
                    else:
                        data = method(envId)
                try: sbgrp.create_dataset(name_of_dataset, data=data)
                except (ValueError, TypeError) as e:
                    print("Error: when saving the Evaluator object to a HDF5 file, the dataset named {} (value of type {} and shape {} and dtype {}) couldn't be saved. Skipping...".format(name_of_dataset, type(data), data.shape, data.dtype))  # DEBUG
                    print("Exception:\n", e)  # DEBUG

        # 5. when done, close the file
        h5file.close()

    def loadfromdisk(self, filepath):
        """ Update internal memory of the Evaluator object by loading data the opened HDF5 file.

        .. warning:: FIXME this is not YET implemented!
        """
        # FIXME I just have to fill all the internal matrices from the HDF5 file ?
        raise NotImplementedError

    # --- Get data

    def getPulls(self, policyId, envId=0):
        """Extract mean pulls."""
        return self.pulls[envId][policyId, :] / float(self.repetitions)

    def getBestArmPulls(self, policyId, envId=0):
        """Extract mean best arm pulls."""
        # We have to divide by a arange() = cumsum(ones) to get a frequency
        return self.bestArmPulls[envId][policyId, :] / (float(self.repetitions) * self._times)

    def getRewards(self, policyId, envId=0):
        """Extract mean rewards."""
        return self.rewards[policyId, envId, :] / float(self.repetitions)

    def getAverageWeightedSelections(self, policyId, envId=0):
        """Extract weighted count of selections."""
        weighted_selections = np.zeros(self.horizon)
        for armId, mean in enumerate(self.envs[envId].means):
            mean_selections = self.allPulls[envId][policyId, armId, :] / float(self.repetitions)
            weighted_selections += mean * mean_selections
        return weighted_selections

    def getMaxRewards(self, envId=0):
        """Extract max mean rewards."""
        return np.max(self.rewards[:, envId, :] / float(self.repetitions))

    def getCumulatedRegret_LessAccurate(self, policyId, envId=0):
        """Compute cumulative regret, based on accumulated rewards."""
        # return self._times * self.envs[envId].maxArm - np.cumsum(self.getRewards(policyId, envId))
        return np.cumsum(self.envs[envId].maxArm - self.getRewards(policyId, envId))

    def getCumulatedRegret_MoreAccurate(self, policyId, envId=0):
        """Compute cumulative regret, based on counts of selections and not actual rewards."""
        assert self.moreAccurate, "Error: getCumulatedRegret_MoreAccurate() is only available when using the 'moreAccurate' option (it consumes more memory!)."  # DEBUG
        return np.cumsum(self.envs[envId].maxArm - self.getAverageWeightedSelections(policyId, envId))

    def getCumulatedRegret(self, policyId, envId=0, moreAccurate=None):
        """Using either the more accurate or the less accurate regret count."""
        moreAccurate = moreAccurate if moreAccurate is not None else self.moreAccurate
        # print("Computing the vector of mean cumulated regret with '{}' accurate method...".format("more" if moreAccurate else "less"))  # DEBUG
        if moreAccurate:
            return self.getCumulatedRegret_MoreAccurate(policyId, envId=envId)
        else:
            return self.getCumulatedRegret_LessAccurate(policyId, envId=envId)

    def getLastRegrets_LessAccurate(self, policyId, envId=0):
        """Extract last regrets, based on accumulated rewards."""
        return self.horizon * self.envs[envId].maxArm - self.lastCumRewards[policyId, envId, :]

    def getAllLastWeightedSelections(self, policyId, envId=0):
        """Extract weighted count of selections."""
        all_last_weighted_selections = np.zeros(self.repetitions)
        for armId, mean in enumerate(self.envs[envId].means):
            last_selections = self.lastPulls[envId][policyId, armId, :]
            all_last_weighted_selections += mean * last_selections
        return all_last_weighted_selections

    def getLastRegrets_MoreAccurate(self, policyId, envId=0):
        """Extract last regrets, based on counts of selections and not actual rewards."""
        return self.horizon * self.envs[envId].maxArm - self.getAllLastWeightedSelections(policyId, envId=envId)

    def getLastRegrets(self, policyId, envId=0, moreAccurate=None):
        """Using either the more accurate or the less accurate regret count."""
        moreAccurate = moreAccurate if moreAccurate is not None else self.moreAccurate
        # print("Computing the vector of last cumulated regrets (on repetitions) with '{}' accurate method...".format("more" if moreAccurate else "less"))  # DEBUG
        if moreAccurate:
            return self.getLastRegrets_MoreAccurate(policyId, envId=envId)
        else:
            return self.getLastRegrets_LessAccurate(policyId, envId=envId)

    def getAverageRewards(self, policyId, envId=0):
        """Extract mean rewards (not `rewards` but `cumsum(rewards)/cumsum(1)`."""
        return np.cumsum(self.getRewards(policyId, envId)) / self._times

    def getRewardsSquared(self, policyId, envId=0):
        """Extract rewards squared."""
        return self.rewardsSquared[policyId, envId, :] / float(self.repetitions)

    def getSTDRegret(self, policyId, envId=0, meanRegret=False):
        """Extract standard deviation of rewards. FIXME experimental! """
        # X = self._times
        # YMAX = self.getMaxRewards(envId=envId)
        # Y = self.getRewards(policyId, envId)
        # Y2 = self.getRewardsSquared(policyId, envId)
        # if meanRegret:  # Cumulated expectation on time
        #     Ycum2 = (np.cumsum(Y) / X)**2
        #     Y2cum = np.cumsum(Y2) / X
        #     assert np.all(Y2cum >= Ycum2), "Error: getSTDRegret found a nan value in the standard deviation (ie a point where Y2cum < Ycum2)."  # DEBUG
        #     stdY = np.sqrt(Y2cum - Ycum2)
        #     YMAX *= 20  # XXX make it look smaller, for the plots
        # else:  # Expectation on nb of repetitions
        #     # https://en.wikipedia.org/wiki/Algebraic_formula_for_the_variance#In_terms_of_raw_moments
        #     # std(Y) = sqrt( E[Y**2] - E[Y]**2 )
        #     # stdY = np.cumsum(np.sqrt(Y2 - Y**2))
        #     stdY = np.sqrt(Y2 - Y**2)
        #     YMAX *= np.log(2 + self.horizon)  # Normalize the std variation
        #     YMAX *= 50  # XXX make it look larger, for the plots
        # # Renormalize this standard deviation
        # # stdY /= YMAX
        allRewards = self.allRewards[policyId, envId, :, :]
        return np.std(np.cumsum(allRewards, axis=0), axis=1)

    def getMaxMinReward(self, policyId, envId=0):
        """Extract amplitude of rewards as maxCumRewards - minCumRewards."""
        return (self.maxCumRewards[policyId, envId, :] - self.minCumRewards[policyId, envId, :]) / (float(self.repetitions) ** 0.5)
        # return self.maxCumRewards[policyId, envId, :] - self.minCumRewards[policyId, envId, :]

    def getRunningTimes(self, envId=0):
        """Get the means and stds and list of running time of the different policies."""
        all_times = [ self.runningTimes[envId][policyId, :] for policyId in range(self.nbPolicies) ]
        means = [ np.mean(times) for times in all_times ]
        stds  = [ np.std(times) for times in all_times ]
        return means, stds, all_times

    def getMemoryConsumption(self, envId=0):
        """Get the means and stds and list of memory consumptions of the different policies."""
        all_memories = [ self.memoryConsumption[envId][policyId, :] for policyId in range(self.nbPolicies) ]
        for policyId in range(self.nbPolicies):
            all_memories[policyId] = [ m for m in all_memories[policyId] if m > 0 ]
        means = [ np.mean(memories) for memories in all_memories ]
        stds  = [ np.std(memories) for memories in all_memories ]
        return means, stds, all_memories

    # --- Plotting methods

    def plotRegrets(self, envId,
                    savefig=None, meanRegret=False,
                    plotSTD=False, plotMaxMin=False,
                    semilogx=False, semilogy=False, loglog=False,
                    normalizedRegret=False, drawUpperBound=False,
                    moreAccurate=None
                    ):
        """Plot the centralized cumulated regret, support more than one environments (use evaluators to give a list of other environments). """
        moreAccurate = moreAccurate if moreAccurate is not None else self.moreAccurate
        fig = plt.figure()
        ymin = 0
        colors = palette(self.nbPolicies)
        markers = makemarkers(self.nbPolicies)
        X = self._times - 1
        plot_method = plt.loglog if loglog else plt.plot
        plot_method = plt.semilogy if semilogy else plot_method
        plot_method = plt.semilogx if semilogx else plot_method
        for i, policy in enumerate(self.policies):
            if meanRegret:
                Y = self.getAverageRewards(i, envId)
            else:
                Y = self.getCumulatedRegret(i, envId, moreAccurate=moreAccurate)
                if normalizedRegret:
                    Y /= np.log(X + 2)   # XXX prevent /0
            ymin = min(ymin, np.min(Y))
            lw = 5 if ('$N=' in policy.__cachedstr__ or 'Aggr' in policy.__cachedstr__ or 'CORRAL' in policy.__cachedstr__ or 'LearnExp' in policy.__cachedstr__ or 'Exp4' in policy.__cachedstr__) else 3
            if semilogx or loglog:
                # FIXED for semilogx plots, truncate to only show t >= 100
                X_to_plot_here = X[X >= 100]
                Y_to_plot_here = Y[X >= 100]
                plot_method(X_to_plot_here[::self.delta_t_plot], Y_to_plot_here[::self.delta_t_plot], label=policy.__cachedstr__, color=colors[i], marker=markers[i], markevery=(i / 50., 0.1), lw=lw)
            else:
                plot_method(X[::self.delta_t_plot], Y[::self.delta_t_plot], label=policy.__cachedstr__, color=colors[i], marker=markers[i], markevery=(i / 50., 0.1), lw=lw)
            if semilogx or loglog:  # Manual fix for issue https://github.com/SMPyBandits/SMPyBandits/issues/38
                plt.xscale('log')
            if semilogy or loglog:  # Manual fix for issue https://github.com/SMPyBandits/SMPyBandits/issues/38
                plt.yscale('log')
            # Print standard deviation of regret
            if plotSTD and self.repetitions > 1:
                stdY = self.getSTDRegret(i, envId, meanRegret=meanRegret)
                if normalizedRegret:
                    stdY /= np.log(2 + X)
                plt.fill_between(X[::self.delta_t_plot], Y[::self.delta_t_plot] - stdY[::self.delta_t_plot], Y[::self.delta_t_plot] + stdY[::self.delta_t_plot], facecolor=colors[i], alpha=0.2)
            # Print amplitude of regret
            if plotMaxMin and self.repetitions > 1:
                MaxMinY = self.getMaxMinReward(i, envId) / 2.
                if normalizedRegret:
                    MaxMinY /= np.log(2 + X)
                plt.fill_between(X[::self.delta_t_plot], Y[::self.delta_t_plot] - MaxMinY[::self.delta_t_plot], Y[::self.delta_t_plot] + MaxMinY[::self.delta_t_plot], facecolor=colors[i], alpha=0.2)
        plt.xlabel(r"Time steps $t = 1...T$, horizon $T = {}${}".format(self.horizon, self.signature))
        lowerbound = self.envs[envId].lowerbound()
        lowerbound_sparse = self.envs[envId].lowerbound_sparse()
        if not (semilogx or semilogy or loglog):
            print("\nThis MAB problem has: \n - a [Lai & Robbins] complexity constant C(mu) = {:.3g} for 1-player problem... \n - a Optimal Arm Identification factor H_OI(mu) = {:.2%} ...".format(lowerbound, self.envs[envId].hoifactor()))  # DEBUG
            if self.envs[envId]._sparsity is not None and not np.isnan(lowerbound_sparse):
                print("\n- a [Kwon et al] sparse lower-bound with s = {} non-negative arm, C'(mu) = {:.3g}...".format(self.envs[envId]._sparsity, lowerbound_sparse))  # DEBUG
        if not meanRegret:
            plt.ylim(ymin, plt.ylim()[1])
        # Get a small string to add to ylabel
        ylabel2 = r"%s%s" % (r", $\pm 1$ standard deviation" if (plotSTD and not plotMaxMin) else "", r", $\pm 1$ amplitude" if (plotMaxMin and not plotSTD) else "")
        if meanRegret:
            # We plot a horizontal line ----- at the best arm mean
            plt.plot(X[::self.delta_t_plot], self.envs[envId].maxArm * np.ones_like(X)[::self.delta_t_plot], 'k--', label="Mean of the best arm = ${:.3g}$".format(self.envs[envId].maxArm))
            legend()
            plt.ylabel(r"Mean reward, average on time $\tilde{r}_t = \frac{1}{t} \sum_{s = 0}^{t-1}$ %s%s" % (r"$\sum_{k=1}^{%d} \mu_k\mathbb{E}_{%d}[T_k(s)]$" % (self.envs[envId].nbArms, self.repetitions) if moreAccurate else r"$\mathbb{E}_{%d}[r_s]$" % (self.repetitions), ylabel2))
            if not self.envs[envId].isDynamic:
                plt.ylim(0.94 * self.envs[envId].minArm, 1.06 * self.envs[envId].maxArm)
            plt.title("Mean rewards for different bandit algorithms, averaged ${}$ times\n${}$ arms{}: {}".format(self.repetitions, self.envs[envId].nbArms, self.envs[envId].str_sparsity(), self.envs[envId].reprarms(1, latex=True)))
        elif normalizedRegret:
            if self.plot_lowerbound:
                # We also plot the Lai & Robbins lower bound
                plt.plot(X[::self.delta_t_plot], lowerbound * np.ones_like(X)[::self.delta_t_plot], 'k-', label="[Lai & Robbins] lower bound = ${:.3g}$".format(lowerbound), lw=3)
                # We also plot the Kwon et al lower bound
                if self.envs[envId]._sparsity is not None and not np.isnan(lowerbound_sparse):
                    plt.plot(X[::self.delta_t_plot], lowerbound_sparse * np.ones_like(X)[::self.delta_t_plot], 'k--', label="[Kwon et al.] lower bound, $s = {}$, $= {:.3g}$".format(self.envs[envId]._sparsity, lowerbound_sparse), lw=3)
            legend()
            plt.ylabel(r"Normalized cumulated regret $\frac{R_t}{\log t} = \frac{t}{\log t} \mu^* - \frac{1}{\log t}\sum_{s = 0}^{t-1}$ %s%s" % (r"$\sum_{k=1}^{%d} \mu_k\mathbb{E}_{%d}[T_k(s)]$" % (self.envs[envId].nbArms, self.repetitions) if moreAccurate else r"$\mathbb{E}_{%d}[r_s]$" % (self.repetitions), ylabel2))
            plt.title("Normalized cumulated regrets for different bandit algorithms, averaged ${}$ times\n${}$ arms{}: {}".format(self.repetitions, self.envs[envId].nbArms, self.envs[envId].str_sparsity(), self.envs[envId].reprarms(1, latex=True)))
        else:
            if drawUpperBound and not (semilogx or loglog):
                # Experiment to print also an upper bound: it is CRAZILY huge!!
                lower_amplitudes = np.asarray([arm.lower_amplitude for arm in self.envs[envId].arms])
                amplitude = np.max(lower_amplitudes[:, 1])
                maxVariance = max([p * (1 - p) for p in self.envs[envId].means])
                K = self.envs[envId].nbArms
                upperbound = 76 * np.sqrt(maxVariance * K * X) + amplitude * K
                plt.plot(X[::self.delta_t_plot], upperbound[::self.delta_t_plot], 'r-', label=r"Minimax upper-bound for kl-UCB++", lw=3)
            # FIXED for semilogx plots, truncate to only show t >= 100
            if semilogx or loglog:
                X = X[X >= 100]
            else:
                X = X[X >= 1]
            if self.plot_lowerbound:
                # We also plot the Lai & Robbins lower bound
                plt.plot(X[::self.delta_t_plot], lowerbound * np.log(X)[::self.delta_t_plot], 'k-', label=r"[Lai & Robbins] lower bound = ${:.3g}\; \log(t)$".format(lowerbound), lw=3)
                # We also plot the Kwon et al lower bound
                if self.envs[envId]._sparsity is not None and not np.isnan(lowerbound_sparse):
                    plt.plot(X[::self.delta_t_plot], lowerbound_sparse * np.ones_like(X)[::self.delta_t_plot], 'k--', label=r"[Kwon et al.] lower bound, $s = {}$, $= {:.3g} \; \log(t)$".format(self.envs[envId]._sparsity, lowerbound_sparse), lw=3)
            legend()
            plt.ylabel(r"Cumulated regret $R_t = t \mu^* - \sum_{s = 0}^{t-1}$ %s%s" % (r"$\sum_{k=1}^{%d} \mu_k\mathbb{E}_{%d}[T_k(s)]$" % (self.envs[envId].nbArms, self.repetitions) if moreAccurate else r"$\mathbb{E}_{%d}[r_s]$" % (self.repetitions), ylabel2))
            plt.title("Cumulated regrets for different bandit algorithms, averaged ${}$ times\n${}$ arms{}: {}".format(self.repetitions, self.envs[envId].nbArms, self.envs[envId].str_sparsity(), self.envs[envId].reprarms(1, latex=True)))
        show_and_save(self.showplot, savefig, fig=fig, pickleit=True)
        return fig

    def plotBestArmPulls(self, envId, savefig=None):
        """Plot the frequency of pulls of the best channel.

        - Warning: does not adapt to dynamic settings!
        """
        fig = plt.figure()
        colors = palette(self.nbPolicies)
        markers = makemarkers(self.nbPolicies)
        X = self._times[2:]
        for i, policy in enumerate(self.policies):
            Y = self.getBestArmPulls(i, envId)[2:]
            lw = 5 if ('$N=' in policy.__cachedstr__ or 'Aggr' in policy.__cachedstr__ or 'CORRAL' in policy.__cachedstr__ or 'LearnExp' in policy.__cachedstr__ or 'Exp4' in policy.__cachedstr__) else 3
            plt.plot(X[::self.delta_t_plot], Y[::self.delta_t_plot], label=policy.__cachedstr__, color=colors[i], marker=markers[i], markevery=(i / 50., 0.1), lw=lw)
        legend()
        plt.xlabel(r"Time steps $t = 1...T$, horizon $T = {}${}".format(self.horizon, self.signature))
        add_percent_formatter("yaxis", 1.0)
        plt.ylabel(r"Frequency of pulls of the optimal arm")
        plt.title("Best arm pulls frequency for different bandit algorithms, averaged ${}$ times\n${}$ arms{}: {}".format(self.repetitions, self.envs[envId].nbArms, self.envs[envId].str_sparsity(), self.envs[envId].reprarms(1, latex=True)))
        show_and_save(self.showplot, savefig, fig=fig, pickleit=True)
        return fig

    def plotRunningTimes(self, envId=0, savefig=None, maxNbOfLabels=30,
            base=1, unit="seconds"
        ):
        """Plot the running times of the different policies, as a box plot for each."""
        means, _, all_times = self.getRunningTimes(envId=envId)
        # order by increasing mean time
        index_of_sorting = np.argsort(means)
        labels = [ policy.__cachedstr__ for policy in self.policies ]
        labels = [ labels[i] for i in index_of_sorting ]
        all_times = [ np.asarray(all_times[i]) / float(base) for i in index_of_sorting ]
        fig = plt.figure()
        if len(labels) < maxNbOfLabels:
            plt.boxplot(all_times, labels=labels)
            locs, labels = plt.xticks()
            plt.subplots_adjust(bottom=0.30)
            plt.xticks(locs, labels, rotation=30)  # XXX See https://stackoverflow.com/a/37708190/
        else:
            plt.boxplot(all_times)
        plt.xlabel("Policies{}".format(self.signature))
        plt.ylabel("Running times (in {}), for {} repetitions".format(unit, self.repetitions))
        plt.title("Running times for different bandit algorithms, horizon $T={}$, averaged ${}$ times\n${}$ arms{}: {}".format(self.horizon, self.repetitions, self.envs[envId].nbArms, self.envs[envId].str_sparsity(), self.envs[envId].reprarms(1, latex=True)))
        show_and_save(self.showplot, savefig, fig=fig, pickleit=True)
        return fig

    def plotMemoryConsumption(self, envId=0, savefig=None, maxNbOfLabels=30,
            base=1024, unit="KiB"
        ):
        """Plot the memory consumption of the different policies, as a box plot for each."""
        means, _, all_memories = self.getMemoryConsumption(envId=envId)
        # order by increasing mean memory consumption
        index_of_sorting = np.argsort(means)
        labels = [ policy.__cachedstr__ for policy in self.policies ]
        labels = [ labels[i] for i in index_of_sorting ]
        all_memories = [ np.asarray(all_memories[i]) / float(base) for i in index_of_sorting ]
        fig = plt.figure()
        if len(labels) < maxNbOfLabels:
            plt.boxplot(all_memories, labels=labels)
            locs, labels = plt.xticks()
            plt.subplots_adjust(bottom=0.30)
            plt.xticks(locs, labels, rotation=30)  # XXX See https://stackoverflow.com/a/37708190/
        else:
            plt.boxplot(all_memories)
        plt.xlabel("Policies{}".format(self.signature))
        plt.ylabel("Memory consumption (in {}), for {} repetitions".format(unit, self.repetitions))
        plt.title("Memory consumption for different bandit algorithms, horizon $T={}$, averaged ${}$ times\n${}$ arms{}: {}".format(self.horizon, self.repetitions, self.envs[envId].nbArms, self.envs[envId].str_sparsity(), self.envs[envId].reprarms(1, latex=True)))
        show_and_save(self.showplot, savefig, fig=fig, pickleit=True)
        return fig

    def printFinalRanking(self, envId=0, moreAccurate=None):
        """Print the final ranking of the different policies."""
        assert 0 < self.averageOn < 1, "Error, the parameter averageOn of a EvaluatorMultiPlayers classs has to be in (0, 1) strictly, but is = {} here ...".format(self.averageOn)  # DEBUG
        print("\nFinal ranking for this environment #{} :".format(envId))
        nbPolicies = self.nbPolicies
        lastY = np.zeros(nbPolicies)
        for i, policy in enumerate(self.policies):
            Y = self.getCumulatedRegret(i, envId, moreAccurate=moreAccurate)
            if self.finalRanksOnAverage:
                lastY[i] = np.mean(Y[-int(self.averageOn * self.horizon)])   # get average value during the last 0.5% of the iterations
            else:
                lastY[i] = Y[-1]  # get the last value
        # Sort lastY and give ranking
        index_of_sorting = np.argsort(lastY)
        for i, k in enumerate(index_of_sorting):
            policy = self.policies[k]
            print("- Policy '{}'\twas ranked\t{} / {} for this simulation (last regret = {:.5g}).".format(policy.__cachedstr__, i + 1, nbPolicies, lastY[k]))
        return lastY, index_of_sorting

    def printRunningTimes(self, envId=0, precision=3):
        """Print the average+-std running time of the different policies."""
        from IPython.core.magics.execution import _format_time
        means, stds, _ = self.getRunningTimes(envId)

        for policyId in np.argsort(means):
            policy = self.policies[policyId]
            print("\nFor policy #{} called '{}' ...".format(policyId, policy))
            mean_time, std_time  = means[policyId], stds[policyId]
            print(u"    {mean} ± {std} per loop (mean ± std. dev. of {runs} run{run_plural})"
                .format(
                    runs = self.repetitions,
                    run_plural = "" if self.repetitions == 1 else "s",
                    mean = _format_time(mean_time, precision),
                    std = _format_time(std_time, precision)
                )
            )

    def printMemoryConsumption(self, envId=0):
        """Print the average+-std memory consumption of the different policies."""
        means, stds, _ = self.getMemoryConsumption(envId)

        for policyId in np.argsort(means):
            policy = self.policies[policyId]
            print("\nFor policy #{} called '{}' ...".format(policyId, policy))
            mean_time, std_time = means[policyId], stds[policyId]
            if self.repetitions <= 1:
                print(u"    {mean} (mean of 1 run)".format(mean=sizeof_fmt(mean_time)))
            else:
                print(u"    {mean} ± {std} (mean ± std. dev. of {runs} runs)".format(runs=self.repetitions, mean=sizeof_fmt(mean_time), std=sizeof_fmt(std_time)))

    def printLastRegrets(self, envId=0, moreAccurate=None):
        """Print the last regrets of the different policies."""
        for policyId, policy in enumerate(self.policies):
            print("\n  For policy #{} called '{}' ...".format(policyId, policy))
            last_regrets = self.getLastRegrets(policyId, envId=envId, moreAccurate=moreAccurate)
            print("  Last regrets (for all repetitions) have:")
            # print("Shape of  last regrets R_T = {}".format(np.shape(last_regrets)))  # DEBUG
            print("Min of    last regrets R_T = {:.3g}".format(np.min(last_regrets)))
            print("Mean of   last regrets R_T = {:.3g}".format(np.mean(last_regrets)))
            print("Median of last regrets R_T = {:.3g}".format(np.median(last_regrets)))
            print("Max of    last regrets R_T = {:.3g}".format(np.max(last_regrets)))
            print("STD of    last regrets R_T = {:.3g}".format(np.std(last_regrets)))

    def plotLastRegrets(self, envId=0,
                        normed=False, subplots=True, nbbins=25, log=False,
                        all_on_separate_figures=False, sharex=False, sharey=False,
                        savefig=None, moreAccurate=None):
        """Plot histogram of the regrets R_T for all policies."""
        N = self.nbPolicies
        if N == 1:
            subplots = False  # no need for a subplot
        colors = palette(N)
        if all_on_separate_figures:
            figs = []
            for policyId, policy in enumerate(self.policies):
                fig = plt.figure()
                plt.title("Histogram of regrets for {}\n${}$ arms{}: {}".format(policy.__cachedstr__, self.envs[envId].nbArms, self.envs[envId].str_sparsity(), self.envs[envId].reprarms(1, latex=True)))
                plt.xlabel("Regret value $R_T$ at the end of simulation, for $T = {}${}".format(self.horizon, self.signature))
                plt.ylabel("{} of observations, ${}$ repetitions".format("Frequency" if normed else "Number", self.repetitions))
                last_regrets = self.getLastRegrets(policyId, envId=envId, moreAccurate=moreAccurate)
                n, bins, patches = plt.hist(last_regrets, density=normed, color=colors[policyId], bins=nbbins)
                addTextForWorstCases(plt, n, bins, patches, normed=normed)
                legend()
                show_and_save(self.showplot, None if savefig is None else "{}__Algo_{}_{}".format(savefig, 1 + policyId, 1 + N), fig=fig, pickleit=True)
                figs.append(fig)
            return figs
        elif subplots:
            nrows, ncols = nrows_ncols(N)
            fig, axes = plt.subplots(nrows, ncols, sharex=sharex, sharey=sharey)
            fig.suptitle("Histogram of regrets for different bandit algorithms\n${}$ arms{}: {}".format(self.envs[envId].nbArms, self.envs[envId].str_sparsity(), self.envs[envId].reprarms(1, latex=True)))
            # XXX See https://stackoverflow.com/a/36542971/
            ax0 = fig.add_subplot(111, frame_on=False)  # add a big axes, hide frame
            ax0.grid(False)  # hide grid
            ax0.tick_params(labelcolor='none', top='off', bottom='off', left='off', right='off')  # hide tick and tick label of the big axes
            # Add only once the ylabel, xlabel, in the middle
            ax0.set_ylabel("{} of observations, ${}$ repetitions".format("Frequency" if normed else "Number", self.repetitions))
            ax0.set_xlabel("Regret value $R_T$ at the end of simulation, for $T = {}${}".format(self.horizon, self.signature))
            for policyId, policy in enumerate(self.policies):
                i, j = policyId % nrows, policyId // nrows
                ax = axes[i, j] if ncols > 1 else axes[i]
                last_regrets = self.getLastRegrets(policyId, envId=envId, moreAccurate=moreAccurate)
                n, bins, patches = ax.hist(last_regrets, density=normed, color=colors[policyId], bins=nbbins, log=log)
                addTextForWorstCases(ax, n, bins, patches, normed=normed)
                ax.vlines(np.mean(last_regrets), 0, min(np.max(n), self.repetitions))  # display mean regret on a vertical line
                ax.set_title(policy.__cachedstr__, fontdict={'fontsize': 'x-small'})  # XXX one of x-large, medium, small, None, xx-large, x-small, xx-small, smaller, larger, large
                ax.tick_params(axis='both', labelsize=10)  # XXX https://stackoverflow.com/a/11386056/
        else:
            fig = plt.figure()
            plt.title("Histogram of regrets for different bandit algorithms\n${}$ arms{}: {}".format(self.envs[envId].nbArms, self.envs[envId].str_sparsity(), self.envs[envId].reprarms(1, latex=True)))
            plt.xlabel("Regret value $R_T$ at the end of simulation, for $T = {}${}".format(self.horizon, self.signature))
            plt.ylabel("{} of observations, ${}$ repetitions".format("Frequency" if normed else "Number", self.repetitions))
            all_last_regrets = []
            labels = []
            for policyId, policy in enumerate(self.policies):
                all_last_regrets.append(self.getLastRegrets(policyId, envId=envId, moreAccurate=moreAccurate))
                labels.append(policy.__cachedstr__)
            ns, bins, patchess = plt.hist(all_last_regrets, label=labels, density=normed, color=colors, bins=nbbins)
            for n, patches in zip(ns, patchess):
                addTextForWorstCases(plt, n, bins, patches, normed=normed)
            legend()
        # Common part
        show_and_save(self.showplot, savefig, fig=fig, pickleit=True)
        return fig


# Helper function for the parallelization

def delayed_play(env, policy, horizon,
                 random_shuffle=random_shuffle, random_invert=random_invert, nb_random_events=nb_random_events,
                 seed=None, allrewards=None, repeatId=0,
                 useJoblib=False):
    """Helper function for the parallelization."""
    start_time = time.time()
    start_memory = getCurrentMemory(thread=useJoblib)
    # Give a unique seed to random & numpy.random for each call of this function
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
    # We have to deepcopy because this function is Parallel-ized
    if random_shuffle or random_invert:
        env = deepcopy(env)    # XXX this uses a LOT of RAM memory!!!
    means = env.means
    if env.isDynamic:
        means = env.newRandomArms()
    policy = deepcopy(policy)  # XXX this uses a LOT of RAM memory!!!

    indexes_bestarm = np.nonzero(np.isclose(env.means, env.maxArm))[0]

    # Start game
    policy.startGame()
    result = Result(env.nbArms, horizon, indexes_bestarm=indexes_bestarm, means=means)  # One Result object, for every policy

    # XXX Experimental support for random events: shuffling or inverting the list of arms, at these time steps
    t_events = [i * int(horizon / float(nb_random_events)) for i in range(nb_random_events)]
    if nb_random_events is None or nb_random_events <= 0:
        random_shuffle = False
        random_invert = False

    prettyRange = tqdm(range(horizon), desc="Time t") if repeatId == 0 else range(horizon)
    for t in prettyRange:
        choice = policy.choice()

        if allrewards is None:
            reward = env.draw(choice, t)
        else:
            reward = allrewards[choice, repeatId, t]

        policy.getReward(choice, reward)

        # Finally we store the results
        result.store(t, choice, reward)

        # XXX Experimental : shuffle the arms at the middle of the simulation
        if random_shuffle and t in t_events:
                indexes_bestarm = env.new_order_of_arm(shuffled(env.arms))
                result.change_in_arms(t, indexes_bestarm)
                if repeatId == 0:
                    print("\nShuffling the arms at time t = {} ...".format(t))  # DEBUG
        # XXX Experimental : invert the order of the arms at the middle of the simulation
        if random_invert and t in t_events:
                indexes_bestarm = env.new_order_of_arm(env.arms[::-1])
                result.change_in_arms(t, indexes_bestarm)
                if repeatId == 0:
                    print("\nInverting the order of the arms at time t = {} ...".format(t))  # DEBUG

    # Print the quality of estimation of arm ranking for this policy, just for 1st repetition
    if repeatId == 0 and hasattr(policy, 'estimatedOrder'):
        order = policy.estimatedOrder()
        print("\nEstimated order by the policy {} after {} steps: {} ...".format(policy, horizon, order))
        print("  ==> Optimal arm identification: {:.2%} (relative success)...".format(weightedDistance(order, env.means, n=1)))
        # print("  ==> Manhattan   distance from optimal ordering: {:.2%} (relative success)...".format(manhattan(order)))
        # # print("  ==> Kendell Tau distance from optimal ordering: {:.2%} (relative success)...".format(kendalltau(order)))
        # # print("  ==> Spearman    distance from optimal ordering: {:.2%} (relative success)...".format(spearmanr(order)))
        # print("  ==> Gestalt     distance from optimal ordering: {:.2%} (relative success)...".format(gestalt(order)))
        print("  ==> Mean distance from optimal ordering: {:.2%} (relative success)...".format(meanDistance(order)))

    # Finally, store running time and consumed memory
    result.running_time = time.time() - start_time
    result.memory_consumption = getCurrentMemory(thread=useJoblib) - start_memory
    return result


# --- Helper for loading a previous Evaluator object

def EvaluatorFromDisk(filepath='/tmp/saveondiskEvaluator.hdf5'):
    """ Create a new Evaluator object from the HDF5 file given in argument."""
    with open(filepath, 'r') as hdf:
        configuration = hdf.configuration
        evaluator = Evaluator(configuration)
        evaluator.loadfromdisk(hdf)
    return evaluator


# --- Utility function

from random import shuffle
from copy import copy


def shuffled(mylist):
    """Returns a shuffled version of the input 1D list. sorted() exists instead of list.sort(), but shuffled() does not exist instead of random.shuffle()...

    >>> from random import seed; seed(1234)  # reproducible results
    >>> mylist = [ 0.1,  0.2,  0.3,  0.4,  0.5,  0.6,  0.7,  0.8,  0.9]
    >>> shuffled(mylist)
    [0.9, 0.4, 0.3, 0.6, 0.5, 0.7, 0.1, 0.2, 0.8]
    >>> shuffled(mylist)
    [0.4, 0.3, 0.7, 0.5, 0.8, 0.1, 0.9, 0.6, 0.2]
    >>> shuffled(mylist)
    [0.4, 0.6, 0.9, 0.5, 0.7, 0.2, 0.1, 0.3, 0.8]
    >>> shuffled(mylist)
    [0.8, 0.7, 0.3, 0.1, 0.9, 0.5, 0.6, 0.2, 0.4]
    """
    copiedlist = copy(mylist)
    shuffle(copiedlist)
    return copiedlist


# --- Debugging

if __name__ == "__main__":
    # Code for debugging purposes.
    from doctest import testmod
    print("\nTesting automatically all the docstring written in each functions of this module :")
    testmod(verbose=True)
