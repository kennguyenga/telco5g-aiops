"""
NRF — Network Repository Function

The 5G service registry. NFs register themselves on startup and other NFs
query NRF to discover endpoints. Equivalent to Consul/Eureka but for 5G.
"""
import time
from typing import Optional
from fastapi import HTTPException
from pydantic import BaseModel
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nf_common import create_nf_app, trace_context_from_request


app, tel, failure = create_nf_app("nrf", 8001)


class NFRegistration(BaseModel):
    nf_type: str           # "amf", "smf", ...
    nf_instance_id: str    # unique per process
    endpoint: str          # http://amf:8004
    capabilities: list[str] = []
    last_heartbeat: float = 0.0


# In-memory NF registry: nf_type -> list of registrations
REGISTRY: dict[str, list[NFRegistration]] = {}


@app.post("/nf-instances")
async def register(reg: NFRegistration):
    """Register an NF instance."""
    reg.last_heartbeat = time.time()
    bucket = REGISTRY.setdefault(reg.nf_type, [])
    # Replace if same instance_id already exists
    bucket[:] = [r for r in bucket if r.nf_instance_id != reg.nf_instance_id]
    bucket.append(reg)
    tel.info(f"NF registered: {reg.nf_type}/{reg.nf_instance_id}",
             nf_type=reg.nf_type, endpoint=reg.endpoint)
    tel.gauge(f"registered_nfs", len(bucket), nf_type=reg.nf_type)
    return {"status": "registered", "instance_id": reg.nf_instance_id}


@app.delete("/nf-instances/{instance_id}")
async def deregister(instance_id: str):
    """Deregister an NF instance."""
    for nf_type, bucket in REGISTRY.items():
        before = len(bucket)
        bucket[:] = [r for r in bucket if r.nf_instance_id != instance_id]
        if len(bucket) < before:
            tel.info(f"NF deregistered: {nf_type}/{instance_id}")
            tel.gauge("registered_nfs", len(bucket), nf_type=nf_type)
            return {"status": "deregistered"}
    raise HTTPException(404, "instance not found")


@app.get("/nf-instances")
async def discover(nf_type: Optional[str] = None):
    """Discover NF instances by type."""
    if nf_type:
        return {"nf_type": nf_type, "instances": REGISTRY.get(nf_type, [])}
    return {"all": REGISTRY}


@app.get("/nf-instances/{nf_type}/health")
async def check_nf_health(nf_type: str):
    """Convenience: check if any instance of this NF type is healthy."""
    instances = REGISTRY.get(nf_type, [])
    healthy = [r for r in instances if (time.time() - r.last_heartbeat) < 60]
    return {
        "nf_type": nf_type,
        "total": len(instances),
        "healthy": len(healthy),
        "instances": healthy,
    }
