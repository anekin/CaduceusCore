"""Derive TPS, TTFT, ITL, and TPOT from cycle counts."""

from dataclasses import asdict, replace
from statistics import mean
from typing import Any

from timing.types import RequestMetrics


class MetricsCollector:
    """Static collector that turns raw cycle counts into latency/throughput metrics.

    All public methods are class methods so callers do not need to instantiate the
    collector. Returned values are plain Python floats/lists/dicts suitable for the
    Dashboard layer.
    """

    @classmethod
    def compute_ttft(
        cls,
        prefill_cycles: int,
        first_decode_cycles: int,
        freq_mhz: int,
    ) -> float:
        """Compute Time-To-First-Token (TTFT) in milliseconds.

        Formula::

            ttft_ms = (prefill_cycles + first_decode_cycles) / (freq_mhz * 1e3)

        The result is engine-only TTFT; queue/network latency is intentionally
        excluded. ``prefill_cycles == 0`` returns the first decode latency only.

        Args:
            prefill_cycles: Cycles consumed by the prefill phase.
            first_decode_cycles: Cycles consumed to generate the first output token.
            freq_mhz: Clock frequency in MHz.

        Returns:
            TTFT in milliseconds. Returns ``0.0`` when ``freq_mhz == 0``.
        """
        if freq_mhz == 0:
            return 0.0
        return (prefill_cycles + first_decode_cycles) / (freq_mhz * 1e3)

    @classmethod
    def compute_tps(
        cls,
        output_tokens: int,
        total_decode_cycles: int,
        freq_mhz: int,
    ) -> float:
        """Compute decode throughput in tokens per second (tok/s).

        Formula::

            tps = output_tokens * freq_mhz * 1e6 / total_decode_cycles

        Args:
            output_tokens: Number of generated output tokens.
            total_decode_cycles: Sum of decode cycles across all output tokens.
            freq_mhz: Clock frequency in MHz.

        Returns:
            Tokens per second. Returns ``0.0`` when ``total_decode_cycles == 0``
            or ``freq_mhz == 0`` to avoid division by zero.
        """
        if total_decode_cycles == 0 or freq_mhz == 0:
            return 0.0
        return output_tokens * freq_mhz * 1e6 / total_decode_cycles

    @classmethod
    def compute_tpot(
        cls,
        decode_cycles_list: list[int],
        freq_mhz: int,
    ) -> float | None:
        """Compute Time-Per-Output-Token (TPOT) in microseconds.

        The first decode cycle is excluded because its latency is captured by TTFT.
        TPOT is the mean of the remaining per-token decode latencies.

        Formula::

            tpot_us = mean(decode_cycles_list[1:]) / freq_mhz

        Args:
            decode_cycles_list: Per-token decode cycle counts.
            freq_mhz: Clock frequency in MHz.

        Returns:
            TPOT in microseconds, or ``None`` when there are fewer than two
            decode tokens (no inter-token average exists). Returns ``None``
            when ``freq_mhz == 0``.
        """
        if len(decode_cycles_list) <= 1 or freq_mhz == 0:
            return None
        return mean(decode_cycles_list[1:]) / freq_mhz

    @classmethod
    def compute_itl(
        cls,
        decode_cycles_list: list[int],
        freq_mhz: int,
    ) -> list[float]:
        """Compute Inter-Token Latency (ITL) for each token pair in microseconds.

        The first decode cycle is excluded because its latency is captured by TTFT.
        Each remaining entry is the latency to generate that token.

        Formula::

            itl_us_list = [decode_cycles_list[i] / freq_mhz for i in 1..N-1]

        Args:
            decode_cycles_list: Per-token decode cycle counts.
            freq_mhz: Clock frequency in MHz.

        Returns:
            List of ITL values in microseconds, or ``[]`` when there are fewer
            than two decode tokens. Returns ``[]`` when ``freq_mhz == 0``.
        """
        if len(decode_cycles_list) <= 1 or freq_mhz == 0:
            return []
        return [cycles / freq_mhz for cycles in decode_cycles_list[1:]]

    @classmethod
    def compute_module_utilization(
        cls,
        module_breakdowns: dict[str, int],
        total_cycles: int,
    ) -> dict[str, float]:
        """Compute per-module cycle utilization as a percentage of total cycles.

        Formula::

            utilization_pct[module] = module_cycles / total_cycles * 100

        Args:
            module_breakdowns: Mapping from module name to cycle count.
            total_cycles: Total cycle count to normalize against.

        Returns:
            Mapping from module name to utilization percentage. Returns ``0.0``
            for every module when ``total_cycles == 0``.
        """
        if total_cycles == 0:
            return {name: 0.0 for name in module_breakdowns}
        return {
            name: cycles / total_cycles * 100.0
            for name, cycles in module_breakdowns.items()
        }

    @classmethod
    def collect(
        cls,
        request_metrics: RequestMetrics,
        freq_mhz: int,
    ) -> dict[str, Any]:
        """Fill timing fields on a copy of ``request_metrics`` and return a dict.

        Computes TTFT (converted to microseconds), TPS, TPOT, and ITL from the
        raw cycle counts already present in ``request_metrics``. The input is not
        mutated; a shallow copy is updated and returned as a plain dict.

        The returned dict contains at minimum:
        ``prompt_len``, ``output_tokens``, ``prefill_cycles``,
        ``decode_cycles_per_token``, ``ttft_us``, ``tps``, ``tpot_us``,
        ``itl_us_list``.

        Args:
            request_metrics: Raw request metrics with cycle counts.
            freq_mhz: Clock frequency in MHz.

        Returns:
            Complete metrics dictionary suitable for serialization by Dashboard.
        """
        decode_cycles = request_metrics.decode_cycles_per_token
        first_decode_cycles = decode_cycles[0] if decode_cycles else 0
        total_decode_cycles = sum(decode_cycles)

        ttft_ms = cls.compute_ttft(
            request_metrics.prefill_cycles,
            first_decode_cycles,
            freq_mhz,
        )

        filled = replace(
            request_metrics,
            ttft_us=ttft_ms * 1000.0,
            tps=cls.compute_tps(
                request_metrics.output_tokens,
                total_decode_cycles,
                freq_mhz,
            ),
            tpot_us=cls.compute_tpot(decode_cycles, freq_mhz),
            itl_us_list=cls.compute_itl(decode_cycles, freq_mhz),
        )

        return asdict(filled)
