# stdlib
import random
import sys
from textwrap import dedent

# third party
import pytest

# syft absolute
import syft as sy
from syft import ActionObject
from syft import syft_function
from syft import syft_function_single_use
from syft.service.response import SyftError
from syft.service.response import SyftSuccess


@pytest.fixture
def node():
    random.seed()
    name = f"nested_job_test_domain-{random.randint(0,1000)}"
    _node = sy.orchestra.launch(
        name=name,
        dev_mode=True,
        reset=True,
        n_consumers=3,
        create_producer=True,
        queue_port=None,
        in_memory_workers=True,
    )
    # startup code here
    yield _node
    # Cleanup code
    _node.land()


# @pytest.mark.flaky(reruns=5, reruns_delay=1)
@pytest.mark.skipif(sys.platform == "win32", reason="does not run on windows")
def test_nested_jobs(node):
    client = node.login(email="info@openmined.org", password="changethis")

    res = client.register(name="a", email="aa@b.org", password="c", password_verify="c")
    assert isinstance(res, SyftSuccess)
    ds_client = node.login(email="aa@b.org", password="c")
    ## Dataset

    x = ActionObject.from_obj([1, 2])
    x_ptr = x.send(client)

    ## aggregate function
    @sy.syft_function()
    def aggregate_job(job_results):
        return sum(job_results)

    aggregate_job.code = dedent(aggregate_job.code)
    res = ds_client.code.submit(aggregate_job)

    ## Batch function
    @syft_function()
    def process_batch(batch):
        print(f"starting batch {batch}")
        return batch + 1

    process_batch.code = dedent(process_batch.code)

    res = ds_client.code.submit(process_batch)
    print(res)

    ## Main function

    @syft_function_single_use(x=x_ptr)
    def process_all(domain, x):
        job_results = []
        for elem in x:
            batch_job = domain.launch_job(process_batch, batch=elem)
            job_results += [batch_job.result]

        job = domain.launch_job(aggregate_job, job_results=job_results)
        return job.result

    process_all.code = dedent(process_all.code)
    assert process_all.worker_pool_name is None

    # Approve & run
    res = ds_client.code.request_code_execution(process_all)
    print(res)
    assert not isinstance(res, SyftError)

    assert ds_client.code[-1].worker_pool_name is not None
    client.requests[-1].approve(approve_nested=True)

    job = ds_client.code.process_all(x=x_ptr, blocking=False)

    job.wait(timeout=0)

    assert len(job.subjobs) == 3

    assert job.wait(timeout=60).get() == 5
    sub_results = [j.wait(timeout=60).get() for j in job.subjobs]
    assert set(sub_results) == {2, 3, 5}

    job = client.jobs[-1]
    assert job.job_worker_id is not None
