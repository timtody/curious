from experiment import Experiment, CountCollisionsAgent

from logger import Logger
from utils import get_conf, GraphWindow

import time
import os
import numpy as np


np.set_printoptions(suppress=True)

os.environ["WANDB_MODE"] = "dryrun"
os.environ["WANDB_DISABLE_CODE"] = "true"


class Exp(Experiment):
    def run(self):
        sensors = self.env.skin_forearm
        self.win = GraphWindow(
            [
                label
                for tup in [
                    (f"sensor_{i}_x", f"sensor_{i}_y", f"sensor_{i}_z")
                    for i in range(len(sensors))
                ]
                for label in tup
            ],
            3,
            4,
            autoscale=False,
        )

        start = time.time()
        for i in range(10000):
            # input()
            # obs, *_ = self.env.step([0, 0, 0, 0, 0, -1, -0.3])
            obs, *_ = self.env.step(self.env.action_space.sample())
            readings = [e for skin_sensor in sensors for e in skin_sensor.read()[0]]
            self.win.update(*readings)
            # print(obs)
            # print(len(obs))
            # exit(1)
            self_collision = self.env.check_collision_with_self()
            other_collision = self.env.check_collision()
            dynamic_collision = self.env.check_collision_with_dynamic()

        end = time.time()
        print("took", end - start, "s")


cnf = get_conf("conf/main.yaml")
Logger(cnf)
exp = Exp(cnf, 0, mode="bruh")
exp.run()
