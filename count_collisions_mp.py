from experiment import Experiment
import pickle
import plotly.graph_objects as go
import numpy as np
from conf import get_conf
from multiprocessing import Array, Process
from logger import Logger


def run(rank, cnf, mode, results):
    if mode == "notrain":
        cnf.main.train = False
    else:
        cnf.main.train = True
        cnf.env.state_size = mode
    exp = Experiment(cnf)
    n_collisions = 0
    # start the experiment
    if rank == 0:
        print("Starting mode", mode)
    n_collisions, = exp.run([lambda x: x["collided"]],
                            log=True if rank == 0 else False)
    results[rank] = n_collisions


def run_mode_mp(mode, cnf):
    processes = []
    results = Array('d', range(cnf.mp.n_procs))
    for rank in range(cnf.mp.n_procs):
        p = Process(target=run, args=(rank, cnf, mode, results))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()

    return results[:]


if __name__ == "__main__":
    # get config setup
    cnf = get_conf("conf/main.yaml")
    log = Logger(cnf)
    results = []
    state_modes = ["notrain", "tac", "prop", "audio", "all"]
    for mode in state_modes:
        results.append(run_mode_mp(mode, cnf))
    results = np.array(results)
    with open(f"data/{cnf.log.name}_n_collisions.p", "wb") as f:
        pickle.dump(results, f)
    fig = go.Figure([
        go.Bar(x=state_modes,
               y=np.mean(results, axis=1),
               error_y=dict(type='data', array=np.std(results, axis=1)))
    ])
    fig.write_html(f"data/{cnf.log.name}_n_collisions_plot.html")
    fig.show()
