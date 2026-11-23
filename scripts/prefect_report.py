"""Evidence from the Prefect API: the latest runs of env_health and saga_audit, with their artifacts.

Ported from wandersync scripts/ingestion_report.py (read runs + artifacts back through the Prefect client).
  PREFECT_API_URL=http://localhost:4200/api prefect/.venv/bin/python scripts/prefect_report.py [runs-per-flow]
"""
import asyncio
import json
import sys

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import (
    ArtifactFilter,
    ArtifactFilterFlowRunId,
    FlowFilter,
    FlowFilterName,
)
from prefect.client.schemas.sorting import FlowRunSort

PER_FLOW = int(sys.argv[1]) if len(sys.argv) > 1 else 3


async def main():
    async with get_client() as c:
        for name in ("env_health", "saga_audit"):
            runs = await c.read_flow_runs(flow_filter=FlowFilter(name=FlowFilterName(any_=[name])),
                                          sort=FlowRunSort.START_TIME_DESC, limit=PER_FLOW)
            print(f"\n# {name}: last {len(runs)} runs")
            for r in runs:
                secs = (r.end_time - r.start_time).total_seconds() if r.start_time and r.end_time else None
                when = f"{r.start_time:%Y-%m-%d %H:%M:%S}Z" if r.start_time else "not started"
                print(f"\n## {r.name}  state={r.state_name}  start={when}  duration={secs and round(secs, 1)}s  "
                      f"scheduled={r.auto_scheduled}")
                arts = await c.read_artifacts(artifact_filter=ArtifactFilter(flow_run_id=ArtifactFilterFlowRunId(any_=[r.id])))
                for a in (a for a in arts if a.key):  # unkeyed ones are Prefect's own result records
                    print(f"   [{a.type}] {a.key}: {a.description or ''}")
                    if a.type == "table":
                        for row in json.loads(a.data) if isinstance(a.data, str) else a.data:
                            print("     ", row)
                    elif a.type == "markdown":
                        print("     " + str(a.data).replace("\n", "\n      "))


asyncio.run(main())
