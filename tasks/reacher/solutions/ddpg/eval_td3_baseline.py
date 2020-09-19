import os
import torch
from agents.ddpg_agent import DDPGAgent
from agents.policies.td3_policy import TD3Policy
from agents.models.components.noise import GaussianNoise
from agents.memory.memory import Memory
from tools.lr_schedulers import DummyLRScheduler
from tasks.reacher.solutions.utils import get_simulator, STATE_SIZE, ACTION_SIZE, BRAIN_NAME
from tasks.reacher.solutions.ddpg import SOLUTIONS_CHECKPOINT_DIR
from tools.parameter_scheduler import ParameterScheduler
from agents.models.components.noise import OUNoise
from tools.rl_constants import ExperienceBatch, RandomBrainAction
from tools.rl_constants import BrainSet, Brain
from tools.rl_constants import RandomBrainAction
from agents.models.components.mlp import MLP
from agents.models.components.critics import Critic
from tools.layer_initializations import init_layer_within_range, init_layer_inverse_root_fan_in


NUM_AGENTS = 20
NUM_EPISODES = 200
SEED = 0
BATCH_SIZE = 128
REPLAY_BUFFER_SIZE = int(1e6)
GAMMA = 0.99            # discount factor
TAU = 5e-3              # for soft update of target parameters
N_LEARNING_ITERATIONS = 10     # number of learning updates
UPDATE_FREQUENCY = 20       # every n time step do update
MAX_T = 1000
CRITIC_WEIGHT_DECAY = 0.0  # 1e-2
ACTOR_WEIGHT_DECAY = 0.0
LR_ACTOR = 3e-4  # learning rate of the actor
LR_CRITIC = 3e-4  # learning rate of the critic
POLICY_UPDATE_FREQUENCY = 2
WARMUP_STEPS = int(5e3)
MIN_PRIORITY = 1e-3
DROPOUT = None
BATCHNORM = False

SAVE_TAG = 'td3_baseline'
ACTOR_CHECKPOINT = os.path.join(SOLUTIONS_CHECKPOINT_DIR, f'{SAVE_TAG}_actor_checkpoint.pth')
CRITIC_CHECKPOINT = os.path.join(SOLUTIONS_CHECKPOINT_DIR, f'{SAVE_TAG}_critic_checkpoint.pth')


def get_solution_brain_set(actor_network: torch.nn.Module, critic_network: torch.nn.Module):
    solution_agent = DDPGAgent(
        state_shape=STATE_SIZE,
        action_size=ACTION_SIZE,
        random_seed=SEED,
        memory_factory=lambda: memory,
        actor_model_factory=lambda: actor_network,
        critic_model_factory=lambda: critic_network,
        actor_optimizer_factory=lambda params: torch.optim.Adam(params, lr=LR_ACTOR, weight_decay=ACTOR_WEIGHT_DECAY),
        critic_optimizer_factory=lambda params: torch.optim.Adam(params, lr=LR_CRITIC, weight_decay=CRITIC_WEIGHT_DECAY),
        critic_optimizer_scheduler=lambda x: DummyLRScheduler(x),
        actor_optimizer_scheduler=lambda x: DummyLRScheduler(x),
        policy_factory=lambda: TD3Policy(
            action_dim=ACTION_SIZE,
            noise=OUNoise(size=ACTION_SIZE, seed=SEED),
            seed=SEED,
            random_brain_action_factory=lambda: RandomBrainAction(
                action_dim=ACTION_SIZE,
                num_agents=NUM_AGENTS,
                continuous_actions=True,
                continuous_action_range=(-1, 1),
            )
        ),
        update_frequency=UPDATE_FREQUENCY,
        n_learning_iterations=N_LEARNING_ITERATIONS,
        batch_size=BATCH_SIZE,
        gamma=GAMMA,
        tau=TAU,
        policy_update_frequency=POLICY_UPDATE_FREQUENCY,
    )
    reacher_brain = Brain(
        brain_name=BRAIN_NAME,
        action_size=ACTION_SIZE,
        state_shape=STATE_SIZE,
        observation_type='vector',
        agents=[solution_agent],
    )

    brain_set = BrainSet(brains=[reacher_brain])

    return brain_set


if __name__ == '__main__':

    actor = MLP(
        layer_sizes=(STATE_SIZE, 256, 128, ACTION_SIZE),
        seed=SEED,
        output_function=torch.nn.Tanh(),
        with_batchnorm=True,
        output_layer_initialization_fn=lambda l: init_layer_within_range(l),
        hidden_layer_initialization_fn=lambda l: init_layer_inverse_root_fan_in(l),
        activation_function=torch.nn.LeakyReLU(True),
        dropout=None
    )
    critic = Critic(
        state_featurizer=MLP(layer_sizes=(STATE_SIZE, 128), dropout=DROPOUT, with_batchnorm=BATCHNORM,
                             hidden_layer_initialization_fn=init_layer_inverse_root_fan_in,
                             activation_function=torch.nn.LeakyReLU(True)),
        output_module=MLP(layer_sizes=(128 + ACTION_SIZE, 1), dropout=DROPOUT, with_batchnorm=BATCHNORM,
                          hidden_layer_initialization_fn=init_layer_inverse_root_fan_in,
                          activation_function=torch.nn.LeakyReLU(True)),
        seed=SEED,
    )

    actor.load_state_dict(torch.load(ACTOR_CHECKPOINT))
    critic.load_state_dict(torch.load(CRITIC_CHECKPOINT))

    memory = Memory(buffer_size=REPLAY_BUFFER_SIZE, seed=SEED)

    simulator = get_simulator()

    brain_set = get_solution_brain_set(actor, critic)

    agents, average_score = simulator.evaluate(brain_set, n_episodes=1, max_t=MAX_T)
