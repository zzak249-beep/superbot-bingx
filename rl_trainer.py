"""
RL Trainer — Ray Tune + PPO (RLlib) + TensorTrade
Basado en los snippets de las imágenes, adaptado al bot BingX.

Flujo:
  1. bot.py descarga velas y las guarda en CSV (training.csv / evaluation.csv)
  2. rl_trainer.py entrena un agente PPO con Ray Tune haciendo grid search
     de FC_SIZE, LEARNING_RATE y MINIBATCH_SIZE
  3. El mejor checkpoint se guarda en logs/best_policy/
  4. trade_manager.py puede consultar la política entrenada opcionalmente

Ejecutar de forma independiente:
    python src/rl_trainer.py

O llamarlo desde bot.py pasando los CSV ya descargados.
"""

import os
import logging
import pandas as pd

import ray
from ray import tune
from ray.tune.registry import register_env

# TensorTrade
from tensortrade.feed.core import DataFeed, Stream
from tensortrade.oms.instruments import Instrument
from tensortrade.oms.exchanges import Exchange, ExchangeOptions
from tensortrade.oms.services.execution.simulated import execute_order
from tensortrade.oms.wallets import Wallet, Portfolio
import tensortrade.env.default as default

log = logging.getLogger("RL_TRAINER")

# ------------------------------------------------------------------ #
#  Hiperparámetros (grid search)
# ------------------------------------------------------------------ #
FC_SIZE        = tune.grid_search([[256, 256], [1024], [128, 64, 32]])
LEARNING_RATE  = tune.grid_search([0.001, 0.0005, 0.00001])
MINIBATCH_SIZE = tune.grid_search([5, 10, 20])

# ------------------------------------------------------------------ #
#  Entorno TensorTrade
# ------------------------------------------------------------------ #
def create_env(config: dict):
    """
    Crea el entorno TensorTrade a partir de un CSV de velas.
    config keys:
        csv_filename       : ruta al CSV (columnas: Datetime, Open, High, Low, Close, Volume + features)
        reward_window_size : ventana de la recompensa SimpleProfit (default 7)
        window_size        : ventana de observación del entorno (default 14)
        max_allowed_loss   : pérdida máxima antes de terminar el episodio (default 0.10)
        commission         : comisión por operación (default 0.00035)
    """
    tsse_commission = config.get("commission", 0.00035)

    dataset = pd.read_csv(
        config["csv_filename"],
        parse_dates=["Datetime"],
    ).fillna(method="backfill").fillna(method="ffill")

    price = Stream.source(list(dataset["Close"]), dtype="float").rename("USD-TTRD")

    tsse_options  = ExchangeOptions(commission=tsse_commission)
    tsse_exchange = Exchange("TTSE", service=execute_order, options=tsse_options)(price)

    # Instrumentos y cartera
    USD  = Instrument("USD",  2, "US Dollar")
    TTRD = Instrument("TTRD", 2, "TensorTrade Corp")
    cash   = Wallet(tsse_exchange, 1000 * USD)
    asset  = Wallet(tsse_exchange, 0    * TTRD)
    portfolio = Portfolio(USD, [cash, asset])

    # Feed del renderer (OHLCV)
    renderer_feed = DataFeed([
        Stream.source(list(dataset["Datetime"])).rename("date"),
        Stream.source(list(dataset["Open"]),   dtype="float").rename("open"),
        Stream.source(list(dataset["High"]),   dtype="float").rename("high"),
        Stream.source(list(dataset["Low"]),    dtype="float").rename("low"),
        Stream.source(list(dataset["Close"]),  dtype="float").rename("close"),
        Stream.source(list(dataset["Volume"]), dtype="float").rename("volume"),
    ])

    # Features adicionales (todas las columnas extra del CSV)
    features = []
    for c in dataset.columns[1:]:
        s = Stream.source(list(dataset[c]), dtype="float").rename(dataset[c].name
                          if hasattr(dataset[c], "name") else c)
        features += [s]
    feed = DataFeed(features)
    feed.compile()

    reward_scheme  = default.rewards.SimpleProfit(
        window_size=config.get("reward_window_size", 7)
    )
    action_scheme  = default.actions.BSH(cash=cash, asset=asset)

    env = default.create(
        feed            = feed,
        portfolio       = portfolio,
        action_scheme   = action_scheme,
        reward_scheme   = reward_scheme,
        renderer_feed   = renderer_feed,
        renderer        = [],
        window_size     = config.get("window_size",      14),
        max_allowed_loss= config.get("max_allowed_loss", 0.10),
    )
    return env


# ------------------------------------------------------------------ #
#  Entrenamiento con Ray Tune + PPO
# ------------------------------------------------------------------ #
def run_training(
    training_csv:   str = "logs/training.csv",
    evaluation_csv: str = "logs/evaluation.csv",
    num_iterations: int = 5,
    num_samples:    int = 1,
):
    """
    Lanza el grid search de hiperparámetros.
    Guarda los checkpoints en logs/ray_results/.
    Devuelve la ruta al mejor checkpoint.
    """
    cwd = os.getcwd()

    env_config_training = {
        "window_size":        14,
        "reward_window_size": 7,
        "max_allowed_loss":   0.10,
        "csv_filename":       os.path.join(cwd, training_csv),
    }
    env_config_evaluation = {
        "max_allowed_loss": 1.00,   # durante eval dejamos correr hasta el final
        "csv_filename":     os.path.join(cwd, evaluation_csv),
    }

    ray.init(ignore_reinit_error=True)
    register_env("BingXTradingEnv", create_env)

    analysis = tune.run(
        run_or_experiment = "PPO",
        name              = "BingXSignalBot_PPO",
        metric            = "episode_reward_mean",
        mode              = "max",
        stop              = {"training_iteration": num_iterations},
        config = {
            "env":        "BingXTradingEnv",
            "env_config": env_config_training,
            "log_level":  "WARNING",
            "framework":  "torch",
            "ignore_worker_failures": True,
            "num_workers":         1,
            "num_envs_per_worker": 1,
            "num_gpus":            0,
            "clip_rewards":        True,
            "lr":                  LEARNING_RATE,
            "gamma":               0.50,
            "observation_filter":  "MeanStdFilter",
            "model":               {"fcnet_hiddens": FC_SIZE},
            "sgd_minibatch_size":  MINIBATCH_SIZE,
            "evaluation_interval": 1,
            "evaluation_config": {
                "env_config": env_config_evaluation,
                "explore":    False,
            },
        },
        num_samples           = num_samples,
        keep_checkpoints_num  = 10,
        checkpoint_freq       = 1,
        local_dir             = os.path.join(cwd, "logs/ray_results"),
    )

    best_trial = analysis.get_best_trial("episode_reward_mean", "max", "last")
    best_checkpoint = analysis.get_best_checkpoint(
        best_trial, metric="episode_reward_mean", mode="max"
    )
    log.info(f"✅  Mejor checkpoint: {best_checkpoint}")
    log.info(f"    Config: {best_trial.config}")

    ray.shutdown()
    return best_checkpoint


# ------------------------------------------------------------------ #
#  Exportar CSV desde klines de BingX (helper para bot.py)
# ------------------------------------------------------------------ #
def klines_to_csv(klines: list[dict], path: str):
    """
    Convierte la lista de klines devuelta por BingXClient.get_klines()
    al formato CSV que espera create_env().
    """
    import csv
    from datetime import datetime

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["Datetime", "Open", "High", "Low", "Close", "Volume"]
        )
        writer.writeheader()
        for k in klines:
            writer.writerow({
                "Datetime": datetime.utcfromtimestamp(k["time"] / 1000).isoformat(),
                "Open":     k["open"],
                "High":     k["high"],
                "Low":      k["low"],
                "Close":    k["close"],
                "Volume":   k["volume"],
            })
    log.info(f"CSV exportado: {path}  ({len(klines)} filas)")


# ------------------------------------------------------------------ #
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    best = run_training(
        training_csv   = "logs/training.csv",
        evaluation_csv = "logs/evaluation.csv",
        num_iterations = 5,
        num_samples    = 1,
    )
    print(f"\nMejor checkpoint guardado en: {best}")
