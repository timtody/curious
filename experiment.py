import torch
import time
import collections
import numpy as np
from env.environment import Env
from agent import Agent
from utils import RewardQueue, ValueQueue
from algo.ppo_cont import PPO, Memory
from algo.models import ICModule
from torch.utils.tensorboard import SummaryWriter
from collections import defaultdict
from abc import abstractmethod


class Experiment:
    def __init__(self, cnf, rank, log=False):
        self.cnf = cnf
        self.log = log
        # setup env
        env = Env(cnf)
        # skip_wrapper = SkipWrapper(cnf.env.skip)
        # self.env = skip_wrapper(env)
        self.env = env

        # pytorch device
        self.device = (
            torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
        )
        # setup agent
        self.action_dim = cnf.env.action_dim
        self.state_dim = env.observation_space.shape[0]
        self.agent = PPO(self.action_dim, self.state_dim, **cnf.ppo)
        self.memory = Memory()

        # setup ICM
        self.icm = ICModule(cnf.env.action_dim, self.state_dim, **cnf.icm).to(
            self.device
        )
        self.icm_transition = collections.namedtuple(
            "icm_trans", ["state", "next_state", "action"]
        )
        self.icm_buffer = []

        # setup experiment variables
        self.global_step = 0
        self.ppo_timestep = 0

        # setup tensorboard
        if not cnf.main.train:
            self.writer = SummaryWriter(f"tb/mode:notrain_rank:{rank}")
        else:
            self.writer = SummaryWriter(f"tb/mode:{cnf.env.state_size}_rank:{rank}")

        # set random seeds
        np.random.seed()
        torch.manual_seed(np.random.randint(9999))

    @abstractmethod
    def run(self, callbacks, log=False):
        pass

    def reset(self):
        for _ in range(100):
            self.env.step(self.env.action_space.sample() * 10)


class CountCollisions(Experiment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # setup logging metrics
        self.n_collisions = 0
        self.n_sounds = 0
        self.reward_sum = 0

        # queues
        self.Q_LEN = self.cnf.main.train_each
        self.reward_Q = RewardQueue(self.Q_LEN, self.cnf.ppo.gamma)
        self.value_Q = ValueQueue(self.Q_LEN)
        self.value_buf = []

        # experiment parameters
        self.episode_len = 500

    def run(self, callbacks=[]):
        state = self.env.reset()
        results = defaultdict(lambda: 0)
        for i in range(self.cnf.main.n_steps):
            self.ppo_timestep += 1
            self.global_step += 1

            # env step
            if self.log and self.global_step % 5000 == 0:
                print("exp in mode", self.cnf.env.mode, "at step", self.global_step)

            if not self.cnf.main.train:
                action = self.env.action_space.sample()
            else:
                action, action_mean, entropy = self.agent.policy_old.act(
                    state.get(), self.memory
                )

            next_state, _, done, info = self.env.step(action)

            # calculate intrinsic reward
            # make icm trans
            trans = self.icm_transition(
                state.get(), next_state.get(), torch.tensor(action)
            )
            # append to buffer
            self.icm_buffer.append(trans)

            # reset environment
            if self.global_step % self.episode_len == 0:
                done = True
                if self.cnf.main.train:
                    self.reset()

            if self.cnf.main.train:
                self.memory.is_terminals.append(done)

            self.value_buf.append(self.agent.get_value(next_state.get()))

            # train agent
            if self.ppo_timestep % self.cnf.main.train_each == 0:

                # train agent
                state_batch, next_state_batch, action_batch = zip(*self.icm_buffer)
                self.icm_buffer = []

                im_loss = self.icm.train_forward(
                    state_batch, next_state_batch, action_batch
                )

                if self.cnf.main.train:
                    self.memory.rewards = im_loss.cpu().numpy()

                    ploss, vloss = self.agent.update(self.memory)
                    self.memory.clear_memory()
                    self.ppo_timestep = 0

                    self.writer.add_scalar("policy loss", ploss, self.global_step)
                    self.writer.add_scalar("value loss", vloss, self.global_step)

                self.reward_sum += im_loss.mean()

                self.writer.add_scalar(
                    "mean reward", self.reward_sum / self.global_step, self.global_step
                )
                self.writer.add_scalar(
                    "cumulative reward", self.reward_sum, self.global_step
                )

                # for s in range(self.cnf.main.train_each):
                #     self.reward_Q.push(im_loss[s])
                #     self.value_Q.push(self.value_buf[s])

                #     if s + self.global_step >= self.Q_LEN:
                #         step = self.global_step - self.cnf.main.train_each + s
                #         self.writer.add_scalars(
                #             "ret_approx", {
                #                 "true_ret": self.reward_Q.get(),
                #                 "app_ret": self.value_Q.get()
                #             }, step)

                self.value_buf = []

            state = next_state

            # receive callback info
            for i, cb in enumerate(callbacks):
                results[i] += cb(info)

            # retrieve metrics
            self.n_collisions += info["collided"]
            self.n_sounds += info["sound"]

            # log to tensorboard
            if self.cnf.main.train:
                # training-only metrics
                self.writer.add_histogram("action_mean", action_mean, self.global_step)
                self.writer.add_scalar("entropy", entropy, self.global_step)

            # rest of metrics
            # self.writer.add_scalar("reward", im_loss, self.global_step)

            self.writer.add_scalar("n_collisions", self.n_collisions, self.global_step)
            self.writer.add_scalar("n_sounds", self.n_sounds, self.global_step)

        self.env.close()
        return results.values()


class GoalReach(Experiment):
    def __init__(self, *args):
        super().__init__(*args)
        # init goal buffer here
        self.episode_len = 250

    def get_loss(self, state, goal):
        return ((state - goal) ** 2).mean()

    def run(self, callbacks, log=False):
        # get a goal
        # goals = []
        print("generating goal")
        for i in range(100):
            goal, *_ = self.env.step([1] * self.cnf.env.action_dim)
        print("done.")

        for i in range(1000):
            self.global_step += 1
            self.ppo_timestep += 1

            state = self.env.reset()
            done = False
            episode_len = 0
            episode_reward = 0
            while not done:
                episode_len += 1
                if episode_len > self.episode_len:
                    done = True
                action, *_ = self.agent.policy_old.act(state.get(), self.memory)
                next_state, *_ = self.env.step(action)

                reward = -self.get_loss(
                    self.icm.get_embedding(next_state.get()),
                    self.icm.get_embedding(goal.get()),
                )
                if reward >= -0.001:
                    print("i've reached the goal")
                    reward += 1
                    done = True

                episode_reward += reward

                self.memory.rewards.append(reward)
                self.memory.is_terminals.append(done)

                if self.ppo_timestep % self.cnf.main.train_each == 0:
                    self.ppo_timestep = 0

                state = next_state

            self.agent.update(self.memory)
            self.memory.clear_memory()

            self.writer.add_scalar("episode reward", episode_reward, self.global_step)
            self.writer.add_scalar("episode len", episode_len, self.global_step)


class CheckActor(Experiment):
    """ Experiment to investigate the
    critic's function"""

    def run(self, callbacks, log=False):
        state = self.env.reset()
        results = defaultdict(lambda: 0)
        mean_reward = 0
        for i in range(self.cnf.main.n_steps):
            if log and i % 5000 == 0:
                print("exp in mode", self.cnf.env.mode, "at step", i)

            self.ppo_timestep += 1

            if not self.cnf.main.train:
                action = self.env.action_space.sample()
            else:
                action, action_mean, entropy = self.agent.policy_old.act(
                    state.get(), self.memory
                )

            next_state, _, done, info = self.env.step(action)

            if self.cnf.main.train:
                im_loss = self.icm.train_forward(state.get(), next_state.get(), action)
                self.memory.rewards.append(im_loss)
                self.memory.is_terminals.append(done)
                mean_reward += im_loss
            state = next_state

            if self.cnf.main.train:
                if self.ppo_timestep % self.cnf.main.train_each == 0:
                    self.agent.update(self.memory)
                    self.memory.clear_memory()
                    self.ppo_timestep = 0

            # receive callback info
            for i, cb in enumerate(callbacks):
                results[i] += cb(info)

            # retrieve metrics
            self.n_collisions += info["collided"]

            # log to tensorboard
            if self.cnf.main.train:
                self.writer.add_scalar("reward", im_loss, self.global_step)
                self.writer.add_scalar(
                    "mean reward", mean_reward / self.global_step, self.global_step
                )

            self.global_step += 1
        self.env.close()
        return results.values()


class NormalizeObs(Experiment):
    def run(self):
        tac = []
        prop = []
        audio = []

        for i in range(100000):
            obs, *_ = self.env.step(self.env.action_space.sample())
            tac.append(obs.get_filtered("touch"))
            prop.append(obs.get_filtered("joint"))
            audio.append(obs.get_audio())

        tac = torch.tensor(tac).float()
        prop = torch.tensor(prop).float()
        audio = torch.tensor(audio).float()

        print(f"tac:\n\tmean:{tac.mean()}\n\tstd:{tac.std()}")
        print(f"prop:\n\tmean:{prop.mean()}\n\tstd:{prop.std()}")
        print(f"audio:\n\tmean:{audio.mean()}\n\tstd:{audio.std()}")


class TestReward(Experiment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        torch.manual_seed(self.cnf.torch.seed)

        from utils import GraphWindow

        self.window = GraphWindow(
            ["reward"]
            + ["left_finger", "right_finger", "left_wrist", "right_wrist", "back"],
            6,
            1,
        )

    def run(self):
        self.window.update(0, 0, 0, 0, 0, 0)
        actions = [[0, 1, 0, 0, 0, 0, 0], [1, 0, 0, 0, 0, 0, 0], [0, -1, 0, 0, 0, 0, 0]]
        actions = [torch.tensor(a) for a in actions]
        state = self.env.reset()
        reward_sum = 0
        for i in range(10000):
            print(i)
            self.agent.policy_old.act(state.get(), self.memory)
            action = actions[0]
            if i > 50:
                action = actions[1]
            if i > 100:
                action = actions[2]
            if i > 180:
                action = actions[0]
            if i > 250:
                action = torch.tensor([0, 1, 0, 0, 0, 0, 0])

            next_state, *_ = self.env.step(action)
            reward = self.icm.train_forward(
                [[state.get()]], [[next_state.get()]], [action]
            )
            reward_sum += reward
            sensors = np.array(self.env.read_force_sensors()).sum(axis=1)
            state = next_state
            self.window.update(reward, *sensors)
        print(reward_sum / 10000)


class Behavior(Experiment):
    def run(self):
        state = self.env.reset()
        for i in range(10000):
            self.ppo_timestep += 1

            action, *_ = self.agent.policy_old.act(state.get(), self.memory)
            next_state, *_ = self.env.step(action)

            if self.cnf.main.train:
                im_loss = self.icm.train_forward(
                    [[state.get()]], [[next_state.get()]], [action]
                )
                self.memory.rewards.append(im_loss)
                self.memory.is_terminals.append(False)

            if (
                self.ppo_timestep % self.cnf.main.train_each == 0
                and self.cnf.main.train
            ):
                print("Training")
                self.agent.update(self.memory)
                self.memory.clear_memory()

            state = next_state


class GoalReachAgent(Experiment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.agent = Agent(self.action_dim, self.state_dim, self.cnf, self.device)

        # experiment parameters
        self.episode_len = 500

    def run(self):
        state = self.env.reset()

        for _ in range(self.cnf.main.n_steps):
            self.ppo_timestep += 1
            self.global_step += 1

            # env step
            action = self.agent.get_action(state.get())
            next_state, _, done, info = self.env.step(action)

            # append data relevant to PPO (reward gets set in .train())
            self.agent.set_is_done(done)

            # append data to agent relevant to ICM
            self.agent.append_icm_transition(state.get(), next_state.get(), action)

            # reset environment
            if self.global_step % self.episode_len == 0:
                done = True
                if self.cnf.main.train:
                    self.reset()

            # train agent
            if self.ppo_timestep % self.cnf.main.train_each == 0:
                self.agent.train()

            self.agent.save_state(999)
            exit(1)

            state = next_state
