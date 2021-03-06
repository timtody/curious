from logger import Logger
from conf import get_conf
from experiment import CountCollisionsAgent
import pickle
import wandb
import numpy as np
import pandas as pd
import seaborn as sns
import multiprocessing as mp
from multiprocessing import Array, Process
from matplotlib import pyplot as plt


sns.set()


def run(rank, cnf, state, results):
    if state == "notrain":
        cnf.main.train = False
    cnf.env.state = state
    # set seeds
    cnf.env.torch_seed = np.random.randint(9999)
    cnf.env.np_seed = np.random.randint(9999)
    exp = CountCollisionsAgent(cnf, rank)
    n_collisions = 0
    # start the experiment
    if rank == 0:
        print("Starting state", state)
    n_collisions, cum_reward = exp.run()
    results[rank] = n_collisions
    results[rank + len(results) // 2] = cum_reward


def run_state(state, cnf):
    processes = []
    results = Array("d", cnf.mp.n_procs * 2)
    for rank in range(cnf.mp.n_procs):
        p = Process(target=run, args=(rank, cnf, state, results))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()

    return results[: len(results) // 2], results[len(results) // 2 :]


if __name__ == "__main__":
    # get config setup
    mp.set_start_method("spawn")
    cnf = get_conf("conf/main.yaml")
    log = Logger(cnf)
    n_collisions = {}
    cum_reward = {}
    states = [
        ["tac", "prop"],
        ["tac", "prop", "random_reward"],
    ]

    for state in states:
        ncol, cumrew = run_state(state, cnf)
        n_collisions[str(state)] = ncol
        cum_reward[str(state)] = cumrew

    n_collisions_df = pd.DataFrame(data=n_collisions)
    cum_reward_df = pd.DataFrame(data=cum_reward)

    wandb.init(config=cnf, project=cnf.wandb.project, name="master")

    n_collisions_df.mean(axis=0).plot.bar(
        yerr=n_collisions_df.std(axis=0), rot=0
    ).set_title("n collisions")

    plt.savefig("data/n_collisions.png")
    plt.clf()

    cum_reward_df.mean(axis=0).plot.bar(
        yerr=cum_reward_df.std(axis=0), rot=0
    ).set_title("cumulative reward")
    plt.savefig("data/cum_reward.png")
    plt.clf()

    n_collisions = np.array(n_collisions)
    with open(f"data/n_collisions.p", "wb") as f:
        pickle.dump(n_collisions, f)

    cumrew = np.array(cumrew)
    with open(f"data/cum_reward.p", "wb") as f:
        pickle.dump(cumrew, f)
