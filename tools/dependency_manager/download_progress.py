"""Monotonic per-component byte counters; no invented overall percentages."""
import time


class DownloadProgress:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.transfers = {}

    def observe(self, event):
        phase = str(event.get("phase", ""))
        if "download_bytes" not in event and not event.get("transfer_finished"):
            return
        component = event.get("component") or phase
        now = self.clock()
        previous = self.transfers.get(component)
        source = event.get("source", previous["source"] if previous else "")
        attempt = event.get("attempt", previous["attempt"] if previous else 1)
        count = max(0, int(event.get("download_bytes", previous["bytes"] if previous else 0)))
        total = event.get("total_bytes", previous["total"] if previous else None)
        total = total if isinstance(total, int) and total > 0 and total >= count else None
        reset = (previous is None or source != previous["source"] or attempt != previous["attempt"]
                 or count < previous["bytes"])
        if reset:
            previous = {"source": source, "attempt": attempt, "bytes": count, "total": total,
                        "time": now, "speed": 0.0, "samples": 0, "stable": False, "active": True}
        else:
            delta = now - previous["time"]
            if delta >= 0.1:
                instant = (count - previous["bytes"]) / delta
                old = previous["speed"]
                previous["speed"] = instant if not previous["samples"] else 0.3 * instant + 0.7 * old
                previous["samples"] += 1
                previous["stable"] = (previous["samples"] >= 3 and previous["speed"] > 0
                                      and abs(instant - previous["speed"]) / previous["speed"] < 0.5)
                previous.update(bytes=count, total=total, time=now)
        if event.get("transfer_finished") or event.get("sha256_verified"):
            previous.update(bytes=count, total=total, active=False, speed=0.0, stable=False)
        self.transfers[component] = previous

    def finish(self):
        for transfer in self.transfers.values():
            transfer.update(active=False, speed=0.0, stable=False)

    def snapshot(self):
        now = self.clock()
        components = {}
        for component, transfer in self.transfers.items():
            speed = transfer["speed"] if transfer["active"] and now - transfer["time"] <= 3 else 0.0
            eta = ((transfer["total"] - transfer["bytes"]) / speed
                   if speed > 0 and transfer["stable"] and transfer["total"] is not None else None)
            components[component] = {"bytes": transfer["bytes"], "total": transfer["total"],
                                     "speed": speed, "eta": eta, "active": transfer["active"]}
        return {"speed": sum(row["speed"] for row in components.values()), "components": components}


def byte_text(value):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
