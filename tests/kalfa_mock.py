"""A permissive stand in for kalfa's std legos: their signatures and facts, no torch.

kalfa registers with its own ``@kalfa.lego``, which derives the kind from the first segment of the URI;
``lego`` below does the same over cirak's ``lego(kind=...)``, so a URI and its kind never drift apart.
"""

from cirak import declare_kinds
from cirak import lego as cirak_lego
from cirak.registry import registry

KINDS = ("source", "split", "pre", "feed", "loader", "layer", "init", "criterion", "objective", "metric",
         "adapter", "optimizer", "schedule", "turn", "trigger", "checkpoint", "rule", "generate", "plot", "lego",
         "strategy", "device")


def lego(uri, target=None, **facts):
    """Register under ``uri`` with the kind its first segment names."""
    facts.setdefault("kind", uri.split("/")[1])
    return cirak_lego(uri, target, **facts)


class Model:
    built = 0

    def __init__(self, graph, seed=None, index=None, init=None, trainable=True, weights=None, models=None):
        Model.built += 1
        self.graph = graph
        self.seed = seed
        self.index = index
        self.init = init
        self.trainable = trainable
        self.weights = weights
        self.models = models
        self.steps = 0

    def __repr__(self):
        return f"Model(seed={self.seed}, index={self.index}, refs={[n.ref for n in self.graph.nodes if n.ref]})"


def register_all():
    if registry.lookup("/source/kalfa/parquet") is not None:
        return
    declare_kinds(*KINDS)
    _data()
    _models()
    _training()
    _components()


def _data():
    lego("/source/kalfa/parquet", lambda path: {"path": path, "rows": 8}, returns="df", alias="parquet")
    lego("/source/kalfa/image_folder", lambda path: {"path": path, "rows": 8}, returns="df", alias="image_folder")
    lego("/source/kalfa/text_lines", lambda path: {"path": path, "rows": 8}, returns="df", alias="text_lines")
    lego("/lego/kalfa/filter", lambda df, query: {**df, "filters": [*df.get("filters", []), query]})
    lego("/lego/kalfa/filter_set", lambda df, set, filters: {**df, "set": set, "set_filters": filters})
    lego("/split/kalfa/random", lambda df, ratios, seed: {"train": df, "valid": df, "test": df},
         returns=["train", "valid", "test"])
    lego("/split/kalfa/sequential", lambda df, ratios, group: {"train": df, "valid": df, "test": df},
         returns=["train", "valid", "test"], refs={"group": "column"}, alias="sequential")
    lego("/split/kalfa/kfold", lambda df, k, fold, val, seed: {"train": df, "valid": df, "test": df},
         returns=["train", "valid", "test"], alias="kfold")
    lego("/lego/kalfa/fit", lambda df, fields, preprocessors, drop, keys: {"fields": list(fields), "drop": drop},
         returns="prep", state=True)
    lego("/lego/kalfa/apply", lambda df, prep, set, keys: {"frame": set, "prep": prep})
    lego("/feed/kalfa/table", lambda frame, frames: {"data": frame}, alias="table")
    lego("/feed/kalfa/window", lambda frame, frames, size, horizon, context=None, group=None: {"data": frame},
         refs={"group": "column"}, alias="window")
    lego("/feed/kalfa/next_token", lambda frame, frames, seq_len: {"data": frame}, alias="next_token")
    lego("/loader/kalfa/torch", lambda data, set, batch: [{"set": set, "size": batch["size"]}] * 2)
    lego("/pre/sklearn/standard_scaler", lambda: "standard_scaler", alias="standard_scaler")
    lego("/pre/kalfa/abs", lambda: "abs", alias="abs")
    lego("/pre/kalfa/log", lambda base, norm: ("log", base, norm), alias="log")
    lego("/pre/kalfa/cast", lambda dtype: ("cast", dtype), alias="cast")
    lego("/pre/kalfa/one_hot", lambda: "one_hot", alias="one_hot")
    lego("/pre/kalfa/label_encoder", lambda: "label_encoder", alias="label_encoder")
    lego("/pre/kalfa/to_tensor", lambda: "to_tensor", alias="to_tensor")
    lego("/pre/kalfa/to_tensor_signed", lambda: "to_tensor_signed", alias="to_tensor_signed")
    lego("/pre/kalfa/resize", lambda size: ("resize", size), alias="resize")
    lego("/pre/kalfa/random_crop_flip", lambda size: ("random_crop_flip", size), alias="random_crop_flip")
    lego("/pre/kalfa/normalize", lambda mean, std: ("normalize", mean, std), alias="normalize")
    lego("/pre/kalfa/char_tokenizer", lambda: "char_tokenizer", state=True, alias="char_tokenizer")
    lego("/pre/kalfa/simclr_aug", lambda size: ("simclr_aug", size), alias="simclr_aug")
    lego("/pre/kalfa/two_views", lambda transform: ("two_views", transform), refs={"transform": "preprocessor"},
         alias="two_views")
    lego("/data/kalfa/class_weights", lambda frame, power=1.0: "weights", alias="class_weights")
    lego("/data/kalfa/vocab_size", lambda prep: 100, alias="vocab_size")
    for name in ("cpu", "cuda", "mps", "auto"):
        lego(f"/device/kalfa/{name}", lambda index=None: name, alias=name)


def _models():
    lego("/builder/kalfa/module", Model)
    lego("/lego/kalfa/clone", lambda model, decay: ("ema", model, decay), state=True)
    lego("/lego/kalfa/pack", lambda items: dict(items))
    lego("/layer/kalfa/linear", lambda out_features, in_features=None: ("linear", in_features, out_features),
         alias="linear")
    lego("/layer/kalfa/linear_relu", lambda out_features, in_features=None: ("linear_relu", out_features),
         alias="linear_relu")
    lego("/layer/kalfa/l1_distance", lambda: "l1", alias="l1_distance")
    lego("/layer/kalfa/unflatten", lambda shape: ("unflatten", shape), alias="unflatten")
    lego("/layer/kalfa/reparam", lambda: "reparam", alias="reparam")
    lego("/layer/torch/linear", lambda in_features, out_features: ("linear", in_features, out_features))
    lego("/layer/torch/concat", lambda dim: ("concat", dim), alias="concat")
    lego("/layer/torch/relu", lambda: "relu", alias="relu")
    lego("/layer/torch/leaky_relu", lambda negative_slope: ("leaky_relu", negative_slope), alias="leaky_relu")
    lego("/layer/torch/dropout", lambda p: ("dropout", p), alias="dropout")
    lego("/layer/torch/flatten", lambda: "flatten", alias="flatten")
    lego("/layer/torch/gru", lambda hidden: ("gru", hidden), alias="gru")
    lego("/layer/torch/last_step", lambda: "last_step", alias="last_step")
    lego("/layer/torch/embedding", lambda num, dim: ("embedding", num, dim), alias="embedding")
    lego("/layer/timm/timm_backbone", lambda name, pretrained=False, pooled=True: ("timm", name, pretrained, pooled),
         alias="timm_backbone")
    lego("/layer/dcgan/critic", lambda channels, cond_dim=None: ("critic", channels, cond_dim))
    lego("/layer/dcgan/generator", lambda in_dim, channels: ("generator", in_dim, channels))
    lego("/layer/unet/unet", lambda channels: ("unet", channels))
    lego("/layer/gpt/gpt", lambda d_model, layers, heads, seq_len: ("gpt", d_model, layers, heads, seq_len))
    lego("/init/torch/normal", lambda std, mean=0.0: ("normal", std, mean), alias="normal")


def _training():
    lego("/optimizer/torch/adam", lambda models, params, schedule, loss: {"models": list(models), "lr": params["lr"],
                                                                          "loss": loss, "schedule": schedule},
         state=True, refs={"loss": "loss", "schedule": "schedule"}, alias="adam")
    lego("/optimizer/torch/adamw", lambda models, params, schedule, loss: {"models": list(models), "lr": params["lr"],
                                                                           "loss": loss, "schedule": schedule},
         state=True, refs={"loss": "loss", "schedule": "schedule"}, alias="adamw")
    lego("/schedule/kalfa/linear_warmup", lambda step, start, end, steps: start, partial=True, alias="linear_warmup")
    lego("/schedule/kalfa/step_decay", lambda step, step_size, gamma: gamma, partial=True, alias="step_decay")
    lego("/schedule/kalfa/warmup_cosine", lambda step, warmup, total: 1.0, partial=True, alias="warmup_cosine")
    lego("/schedule/kalfa/linear_betas", lambda step, steps: 0.0, partial=True, alias="linear_betas")

    lego("/lego/kalfa/const", lambda value: value)
    lego("/lego/kalfa/merge", merge)
    lego("/lego/kalfa/identity", lambda value: value, aliases="value")
    lego("/lego/kalfa/progress", lambda: "progress")
    lego("/lego/kalfa/init_state", init_state, returns="epochs_left", bus=["resume", "device"], mutates=["state"])
    lego("/turn/kalfa/alternating", turn, returns=["models", "optimizers", "emas", "counters", "metrics"],
         mutates=["models", "optimizers", "emas", "counters"], bus=["device", "prep"],
         alias=["alternating", "supervised"], extras=["amp", "grad_clip", "accumulate"])
    lego("/lego/kalfa/evaluate", evaluate, returns="metrics", bus=["device", "prep"])
    lego("/rule/kalfa/effects", lambda rules: dict(rules.get("effects", {})), returns="effects")
    lego("/rule/kalfa/open", lambda rules: {**rules, "slots": []})
    lego("/rule/kalfa/rule", rule, bus=["metrics", "turn_index"], returns="rules")
    lego("/rule/kalfa/stop", stop, returns=["rules", "stop"], bus=["metrics"])
    lego("/lego/kalfa/checkpoint", checkpoint, returns="improved", bus=["metrics", "record"])
    lego("/lego/kalfa/history", log, returns=None, bus=["metrics", "turn_index", "counters_next", "record"])
    lego("/lego/kalfa/save_final", save_final, returns=None, bus=["rules", "record"])
    lego("/lego/kalfa/select", lambda models, emas, which, record=None: {"which": which, "models": models},
         returns="selected", bus=["record"])
    lego("/lego/kalfa/predict", lambda models, composites, loader, prep, predicts, set, record=None:
         {"predicts": predicts, "set": set, "rows": len(loader)}, returns="predictions", bus=["record"])
    lego("/lego/kalfa/run_all", lambda predictions, history, models, plots, keys, predicts, record=None: None,
         returns=None, bus=["record"])
    lego("/lego/kalfa/generate", lambda models, composites, prep, generate, record=None: None,
         returns=None, bus=["record"])

    lego("/checkpoint/kalfa/best", lambda monitor, mode="min": {"policy": "best", "monitor": monitor, "mode": mode},
         alias="best")
    lego("/checkpoint/kalfa/last", lambda: {"policy": "last"}, alias="last")
    lego("/checkpoint/kalfa/snapshot", lambda every: {"policy": "snapshot", "every": every}, alias="snapshot")
    lego("/trigger/kalfa/after_turn", lambda metrics, turn_index, state, at: (turn_index >= at, state),
         partial=True, alias=["after_turn", "after_epoch"])
    lego("/trigger/kalfa/metric_below", lambda metrics, turn_index, state, monitor, value:
         (metrics.get(monitor, 1e9) < value, state), partial=True, alias="metric_below")
    lego("/trigger/kalfa/metric_above", lambda metrics, turn_index, state, monitor, value:
         (metrics.get(monitor, -1e9) > value, state), partial=True, alias="metric_above")
    lego("/trigger/kalfa/plateau", lambda metrics, turn_index, state, monitor, patience, mode="min", min_delta=0.0:
         (False, state), partial=True, alias="plateau")
    lego("/trigger/kalfa/time_budget", lambda metrics, turn_index, state, minutes: (False, state), partial=True,
         alias="time_budget")
    for name in ("grid", "random", "sobol", "optuna"):
        lego(f"/strategy/kalfa/{name}", lambda space, **extra: [], alias=name)


def _components():
    lego("/adapter/kalfa/criterion", lambda criterion: ("criterion", criterion), uses=["predicts"])
    lego("/adapter/kalfa/metric", lambda metric: ("metric", metric), uses=["predicts"])
    for name in ("mse", "mae", "log_cosh"):
        lego(f"/criterion/kalfa/{name}", lambda predictions, targets: 0.0, partial=True, alias=name)
    lego("/criterion/kalfa/huber", lambda predictions, targets, delta: 0.0, partial=True, alias="huber")
    lego("/criterion/kalfa/bce_logits", lambda predictions, targets, pos_weight=None: 0.0, partial=True,
         alias="bce_logits")
    lego("/criterion/kalfa/cross_entropy", lambda predictions, targets, weight=None, label_smoothing=0.0: 0.0,
         partial=True, alias="cross_entropy")
    lego("/objective/kalfa/vae", lambda models, batch, encoder, decoder, recon, w_rec=1.0, kl_schedule=None, step=0:
         {"loss": 0.0, "recon": 0.0, "kl": 0.0}, partial=True,
         refs={"encoder": "model", "decoder": "model", "recon": "criterion", "kl_schedule": "schedule"}, alias="vae")
    lego("/objective/kalfa/distill", lambda models, batch, student, teacher, temperature, alpha:
         {"loss": 0.0, "ce": 0.0, "kl": 0.0}, partial=True, refs={"student": "model", "teacher": "model"},
         alias="distill")
    lego("/objective/kalfa/wgan_gp_d", lambda models, batch, generator, critic, latent, gp_weight=10.0,
         conditional=False, rng=None, **extra: {"loss": 0.0}, partial=True,
         refs={"generator": "model", "critic": "model"}, needs_grad=True, alias="wgan_gp_d")
    lego("/objective/kalfa/wgan_g", lambda models, batch, generator, critic, latent, conditional=False, rng=None,
         **extra: {"loss": 0.0}, partial=True, refs={"generator": "model", "critic": "model"}, alias="wgan_g")
    lego("/objective/kalfa/ddpm", lambda models, batch, model, schedule, rng=None, **extra: {"loss": 0.0},
         partial=True, refs={"model": "model", "schedule": "schedule"}, alias="ddpm")
    lego("/objective/kalfa/ntxent", lambda models, batch, model, temperature=0.5, **extra: {"loss": 0.0},
         partial=True, refs={"model": "model"}, alias="ntxent")
    lego("/objective/myexample/alad_discriminator", lambda models, batch, criterion, latent_dim: {"loss": 0.0},
         partial=True, refs={"criterion": "criterion"},
         needs_models=["encoder", "generator", "dxz", "dxx", "dzz"])
    lego("/objective/myexample/alad_generator",
         lambda models, batch, criterion, latent_dim, feature_matching: {"loss": 0.0}, partial=True,
         refs={"criterion": "criterion"}, needs_models=["encoder", "generator", "dxz", "dxx", "dzz"])

    lego("/metric/kalfa/rmse", lambda: "rmse", state=True, alias="rmse")
    lego("/metric/kalfa/perplexity", lambda: "perplexity", state=True, alias="perplexity")
    lego("/metric/kalfa/recon_error", lambda: "recon_error", state=True, alias="recon_error")
    lego("/metric/kalfa/fid", lambda model, latent, conditional=False, n=1000, **extra: ("fid", model),
         state=True, refs={"model": "model"}, alias="fid")
    lego("/metric/kalfa/sample_writer", lambda sampler, n=16: ("sample_writer", sampler, n),
         refs={"sampler": "generate"}, alias="sample_writer")
    lego("/metric/torchmetrics/accuracy", lambda: "accuracy", state=True, alias="accuracy")
    lego("/metric/torchmetrics/f1", lambda: "f1", state=True, alias="f1")
    lego("/metric/torchmetrics/binary_auroc", lambda: "auroc", state=True, alias="auroc")
    lego("/metric/torchmetrics/binary_average_precision", lambda: "average_precision", state=True,
         alias="average_precision")

    lego("/generate/kalfa/gan_sampler", lambda models, prep, rng, model, latent, conditional=False, n_classes=None,
         n=64, **extra: "samples", partial=True, refs={"model": "model"}, alias="gan_sampler")
    lego("/generate/kalfa/ddpm_sampler", lambda models, prep, rng, model, schedule, shape=None, n=64, **extra:
         "samples", partial=True, refs={"model": "model", "schedule": "schedule"}, alias="ddpm_sampler")
    lego("/generate/kalfa/lm_sampler", lambda models, prep, rng, model, prompt, max_new_tokens=100, temperature=1.0,
         **extra: "samples", partial=True, refs={"model": "model"}, alias="lm_sampler")

    for name in ("loss_curve", "pred_vs_true", "class_histogram", "architecture", "confusion_matrix"):
        lego(f"/plot/kalfa/{name}", lambda series=None: "plot", alias=name)
    lego("/plot/kalfa/forecast_samples", lambda n: ("forecast", n), alias="forecast_samples")
    lego("/plot/kalfa/image_pairs", lambda n: ("image_pairs", n), alias="image_pairs")
    lego("/plot/kalfa/image_grid", lambda n=16: ("image_grid", n), alias="image_grid")
    lego("/plot/kalfa/samples_gif", lambda duration=0.5: ("samples_gif", duration), alias="samples_gif")
    lego("/plot/kalfa/samples_matrix", lambda n: ("samples_matrix", n), alias="samples_matrix")
    for name in ("binary_roc", "binary_precision_recall_curve"):
        lego(f"/plot/torchmetrics/{name}", lambda: "plot", alias=name)


def merge(parts):
    merged = {}
    for key, values in parts.items():
        prefix = {"train_metrics": "train", "valid_metrics": "val", "test_metrics": "test"}[key]
        for name, value in values.items():
            merged[f"{prefix}/{name}"] = value
    return merged


def init_state(state, epochs, steps, resume=None, device=None):
    assert set(state) == {"models", "optimizers", "emas", "counters", "rules"}
    state["counters"]["device"] = device
    return min(epochs, 3)


def turn(models, optimizers, emas, counters, composites, effects, loader, params, extra, losses, metrics, losses_keys,
         metrics_keys, predicts, steps, device=None, prep=None):
    for model in models.values():
        model.steps += len(loader)
    counters["global_step"] += len(loader)
    counters["turn"] += 1
    loss = effects.get("loss", "default")
    return {"models": models, "optimizers": optimizers, "emas": emas, "counters": counters,
            "metrics": {"loss": 1.0 / counters["turn"], "active": loss, "device": device}}


def evaluate(models, emas, composites, counters, effects, loader, set, losses, metrics, losses_keys, metrics_keys,
             predicts, device=None, prep=None):
    return {"score": counters["turn"] + len(loader), "predicts": predicts}


def rule(rules, name, when, set, after, metrics=None, turn_index=None):
    fired, state = when(metrics, turn_index, rules.get(name, {}))
    updated = {**rules, "slots": [*rules["slots"], name]}
    if fired:
        updated["effects"] = {**rules.get("effects", {}), **set}
    return updated


def stop(rules, triggers, metrics=None):
    fired = [bool(trigger(metrics, 0, {})[0]) for trigger in triggers]
    return {"rules": {**rules, "stop": fired}, "stop": any(fired)}


def checkpoint(state, policy, metrics=None, record=None):
    record.append(("checkpoint", sorted(state), policy and policy["policy"]))
    return True


def log(progress, metrics=None, turn_index=None, counters_next=None, record=None):
    record.append(("log", turn_index, metrics["train/active"]))


def save_final(models, optimizers, emas, counters, rules=None, record=None):
    record.append(("final", sorted(models), sorted(optimizers), counters["turn"]))
