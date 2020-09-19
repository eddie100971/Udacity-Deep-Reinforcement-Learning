from unityagents import UnityEnvironment
import numpy as np
from typing import Tuple, Optional, List, Callable, Dict
from copy import deepcopy
import time
import torch
import random
import matplotlib.pyplot as plt
from tools.rl_constants import BrainSet, Action
from tools.scores import Scores
from collections import OrderedDict
from simulation.utils import default_preprocess_brain_actions_for_env_fn, default_step_agents_fn, default_step_episode_agents_fn
from tools.misc import set_seed

plt.style.use('ggplot')
np.set_printoptions(precision=3, linewidth=120)
import warnings
warnings.filterwarnings("ignore")
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


class UnityEnvironmentSimulator:
    """ Helper class for training an agent in a Unity ML-Agents environment """
    def __init__(self, task_name: str, env: UnityEnvironment, seed: int):
        set_seed(seed)
        self.env = env
        self.task_name = task_name

        self.env_info = None
        self.training_scores = None
        self.evaluation_scores = None

    def reset_env(self, train_mode: bool):
        env_info = self.env.reset(train_mode=train_mode)
        self.env_info = env_info

    def get_next_states(self, brain_set: BrainSet):
        brain_states = {}
        for brain_name, brain in brain_set:
            brain_info = self.env_info[brain_name]
            if brain.observation_type == 'vector':
                states = np.array(brain_info.vector_observations)
            elif brain.observation_type == 'visual':
                states = np.array(brain_info.visual_observations)
            else:
                raise ValueError("Invalid observation_type {}".format(brain.observation_type))

            states = brain.preprocess_state_fn(states)
            states = torch.from_numpy(states).to(device).float()
            brain_states[brain_name] = states
        return brain_states

    def step(self, brain_set: BrainSet, brain_states: dict, random_actions: bool = False, preprocess_brain_actions_for_env_fn: Callable = default_preprocess_brain_actions_for_env_fn) -> dict:
        if random_actions:
            brain_actions: Dict[str, List[Action]] = brain_set.get_random_actions(brain_states)
        else:
            brain_actions: Dict[str, List[Action]] = brain_set.get_actions(brain_states)

        actions: Dict[str, np.ndarray] = preprocess_brain_actions_for_env_fn(deepcopy(brain_actions))

        self.env_info = self.env.step(actions)

        next_brain_states = self.get_next_states(brain_set)

        output = {}
        for brain_name in brain_set.names():
            output[brain_name] = {
                'states': brain_states[brain_name],
                'actions': brain_actions[brain_name],
                'next_states': next_brain_states[brain_name],
                'rewards': self.env_info[brain_name].rewards,
                'dones': self.env_info[brain_name].local_done
            }
        return output

    def train(self, brain_set: BrainSet, solved_score: Optional[float] = None,
              n_episodes=2000, max_t=1000, sliding_window_size: int = 100,
              step_agents_fn=default_step_agents_fn,
              step_episode_agents_fn=default_step_episode_agents_fn,
              brain_reward_accumulation_fn=lambda rewards: np.array(rewards),
              episode_reward_accumulation_fn = lambda brain_episode_scores: float(np.mean([np.mean(brain_episode_scores[brain_name]) for brain_name in brain_episode_scores])),
              preprocess_brain_actions_for_env_fn: Callable = default_preprocess_brain_actions_for_env_fn,
              end_episode_critieria = np.all,
              end_of_episode_score_display_fn: Callable = lambda i_episode, episode_aggregated_score, training_scores: '\rEpisode {}\tScore: {:.2f}\tAverage Score: {:.2f}'.format(i_episode, episode_aggregated_score, training_scores.get_mean_sliding_scores()),
              aggregate_end_of_episode_score_fn: Callable = lambda training_scores: training_scores.get_mean_sliding_scores()
              ) -> Tuple[BrainSet, Scores, int, float]:
        """Train the agent in the environment

        Args:
            brain_set (BrainSet): The agent brains to undergo training
            solved_score (float): The score required to be obtained (calculated as the mean
                of the sliding window scores) in order to mark the task as solved
            n_episodes (int): The number of episodes to train over
            max_t (int): The maximum number of timesteps allowed in each episode
            sliding_window_size (int): The number of historical scores to average over
            step_agents_fn (Callable): Method for preparing experiences
            brain_reward_accumulation_fn (Callable):
        Returns:
            agent (Agent): The trained agent
            scores (Scores): Scores object containing all historic and sliding-window scores
        """
        for brain in brain_set.brains():
            for agent in brain.agents:
                agent.set_mode('train')
                agent.set_warmup(False)

        self.training_scores = Scores(window_size=sliding_window_size)

        t_start = time.time()
        for i_episode in range(1, n_episodes + 1):
            self.reset_env(train_mode=True)
            brain_states = self.get_next_states(brain_set)

            brain_episode_scores = OrderedDict([(brain_name, None) for brain_name, brain in brain_set])

            for t in range(max_t):
                next_brain_environment = self.step(brain_set=brain_set, brain_states=brain_states, preprocess_brain_actions_for_env_fn=preprocess_brain_actions_for_env_fn)
                step_agents_fn(brain_set, next_brain_environment, t)

                brain_states = {
                    brain_name: next_brain_environment[brain_name]['next_states']
                    for brain_name in brain_states
                }

                for brain_name in brain_episode_scores:
                    # Brain rewards are a scalar for each agent,
                    # of form next_brain_environment[brain_name]['rewards']=[0.0, 0.0]
                    brain_rewards = brain_reward_accumulation_fn(next_brain_environment[brain_name]['rewards'])
                    if brain_episode_scores[brain_name] is None:
                        brain_episode_scores[brain_name] = brain_rewards
                    else:
                        brain_episode_scores[brain_name] += brain_rewards

                all_dones = []
                for brain_name in brain_set.names():
                    all_dones.extend(next_brain_environment[brain_name]['dones'])

                if end_episode_critieria(all_dones):
                    break

            # Step episode for agents
            step_episode_agents_fn(brain_set, i_episode)

            # Brain episode scores are of form: {'<brain_name>', <output_of_brain_reward_accumulation_fn>]}
            episode_aggregated_score = episode_reward_accumulation_fn(brain_episode_scores)
            self.training_scores.add(episode_aggregated_score)

            if i_episode % 100 == 0:
                end = '\n'
            else:
                end = ""

            print(end_of_episode_score_display_fn(i_episode, episode_aggregated_score, self.training_scores), end=end)
            if solved_score and aggregate_end_of_episode_score_fn(self.training_scores) >= solved_score:
                print("\nTotal Training time = {:.1f} min".format((time.time() - t_start) / 60))
                print('\nEnvironment solved in {:d} episodes!\tAverage Score: {:.2f}'.format(i_episode, self.training_scores.get_mean_sliding_scores()))
                break
        training_time = round(time.time() - t_start)

        return brain_set, self.training_scores, i_episode, training_time

    def warmup(
            self,
            brain_set: BrainSet,
            n_episodes: int,
            max_t: int,
            step_agents_fn: Callable = default_step_agents_fn,
            preprocess_brain_actions_for_env_fn: Callable = default_preprocess_brain_actions_for_env_fn,
            end_episode_critieria=np.all,
    ):
        print("Performing warmup with {} episodes and max_t={}".format(n_episodes, max_t))
        for brain in brain_set.brains():
            for agent in brain.agents:
                agent.set_mode('train')
                agent.set_warmup(True)

        t1 = time.time()
        for i_episode in range(1, n_episodes + 1):
            self.reset_env(train_mode=True)
            brain_states = self.get_next_states(brain_set)
            for t in range(max_t):
                next_brain_environment = self.step(brain_set=brain_set, brain_states=brain_states, random_actions=True, preprocess_brain_actions_for_env_fn=preprocess_brain_actions_for_env_fn)
                step_agents_fn(brain_set, next_brain_environment, t)
                brain_states = {
                    brain_name: next_brain_environment[brain_name]['next_states']
                    for brain_name in brain_states
                }

                all_dones = []
                for brain_name in brain_set.names():
                    all_dones.extend(next_brain_environment[brain_name]['dones'])

                if end_episode_critieria(all_dones):
                    break

                print('\rEpisode {}\tTimestep: {:.2f}'.format(i_episode, t), end="")
        print("Finished warmup in {}s".format(round(time.time() - t1)))

    def evaluate(self, brain_set: BrainSet, n_episodes=5, max_t=1000,
                 brain_reward_accumulation_fn=lambda rewards: np.array(rewards),
                 episode_reward_accumulation_fn=lambda brain_episode_scores: float(
                     np.mean([np.mean(brain_episode_scores[brain_name]) for brain_name in brain_episode_scores])),
                 end_of_episode_score_display_fn: Callable = lambda i_episode, episode_aggregated_score,
                                                                    training_scores: '\rEpisode {}\tScore: {:.2f}\tAverage Score: {:.2f}'.format(
                     i_episode, episode_aggregated_score, training_scores.get_mean_sliding_scores()),
                 sliding_window_size=100,
                 end_episode_critieria=np.all
                 ) -> Tuple[BrainSet, float]:
        """Train the agent in the environment

        Args:
            brain_set (BrainSet): The agent brains to undergo training
            n_episodes (int): The number of episodes to train over
            max_t (int): The maximum number of timesteps allowed in each episode
            brain_reward_accumulation_fn (Callable):
        Returns:
            agent (Agent): The trained agent
            scores (Scores): Scores object containing all historic and sliding-window scores
        """
        for brain in brain_set.brains():
            for agent in brain.agents:
                agent.set_mode('eval')
                agent.set_warmup(False)

        self.evaluation_scores = Scores(window_size=sliding_window_size)

        for i_episode in range(1, n_episodes + 1):
            self.reset_env(train_mode=False)
            brain_states = self.get_next_states(brain_set)

            brain_episode_scores = {brain_name: None for brain_name, brain in brain_set}

            for t in range(max_t):
                next_brain_environment = self.step(brain_set=brain_set, brain_states=brain_states)

                brain_states = {
                    brain_name: next_brain_environment[brain_name]['next_states']
                    for brain_name in brain_states
                }
                for brain_name in brain_episode_scores:
                    scores = brain_reward_accumulation_fn(next_brain_environment[brain_name]['rewards'])
                    if brain_episode_scores[brain_name] is None:
                        brain_episode_scores[brain_name] = scores
                    else:
                        brain_episode_scores[brain_name] += scores

                all_dones = []
                for brain_name in brain_set.names():
                    all_dones.extend(next_brain_environment[brain_name]['dones'])

                if end_episode_critieria(all_dones):
                    break

            episode_aggregated_score = episode_reward_accumulation_fn(brain_episode_scores)
            self.evaluation_scores.add(episode_aggregated_score)
            print(end_of_episode_score_display_fn(i_episode, episode_aggregated_score, self.evaluation_scores), end='\n')
        average_score = self.evaluation_scores.get_mean_sliding_scores()
        return brain_set, average_score

    def get_agent_performance(self, brain_set: BrainSet, n_train_episodes: int = 100, n_eval_episodes=10, sliding_window_size: int = 100, max_t: int = 1000) -> tuple:
        """
        :param brain_set: BrainSet to get performance for
        :param n_train_episodes: Number of episodes to train agent over
        :param n_eval_episodes: Number of evaluation episodes to average over
        :param sliding_window_size:
        :param max_t: Max number of timesteps per episode
        :return: Tuple of performance (Mean epsiode score) and training supplementary information
        """
        t1 = time.time()
        # TODO: Allow for early stopping if performance is poor
        brain_set, training_scores, i_episode, training_time = self.train(
            brain_set=brain_set,
            solved_score=None,
            n_episodes=n_train_episodes,
            max_t=max_t,
            sliding_window_size=sliding_window_size,
        )

        t2 = time.time()

        info = {
            "train_scores": training_scores,
            "train_time": round(t2-t1),
            "n_train_episodes": n_train_episodes,
            "n_eval_episodes": n_eval_episodes,
            "sliding_window_size": sliding_window_size,
            "max_t": max_t,
        }

        performance = float(training_scores.get_mean_sliding_scores())
        return performance, info

    def close(self):
        self.env.close()
