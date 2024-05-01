# stdlib
from datetime import datetime
import threading
import time
from typing import Any
from typing import cast

# third party
import psutil
from result import Err
from result import Ok
from result import Result

# relative
from ...node.credentials import SyftVerifyKey
from ...node.worker_settings import WorkerSettings
from ...serde.deserialize import _deserialize as deserialize
from ...serde.serializable import serializable
from ...service.context import AuthedServiceContext
from ...store.document_store import BaseStash
from ...types.datetime import DateTime
from ...types.uid import UID
from ..job.job_stash import Job
from ..job.job_stash import JobStash
from ..job.job_stash import JobStatus
from ..response import SyftError
from ..response import SyftSuccess
from ..worker.worker_stash import WorkerStash
from .base_queue import AbstractMessageHandler
from .base_queue import BaseQueueManager
from .base_queue import QueueConfig
from .base_queue import QueueConsumer
from .base_queue import QueueProducer
from .queue_stash import QueueItem
from .queue_stash import Status


class MonitorThread(threading.Thread):
    def __init__(
        self,
        queue_item: QueueItem,
        worker: Any,  # should be of type Worker(Node), but get circular import error
        credentials: SyftVerifyKey,
        interval: int = 5,
    ) -> None:
        super().__init__()
        self.interval = interval
        self.stop_requested = threading.Event()
        self.credentials = credentials
        self.worker = worker
        self.queue_item = queue_item

    def run(self) -> None:
        while not self.stop_requested.is_set():
            self.monitor()
            time.sleep(self.interval)

    def monitor(self) -> None:
        # Implement the monitoring logic here
        job = self.worker.job_stash.get_by_uid(
            self.credentials, self.queue_item.job_id
        ).ok()
        if job is None or job.status != JobStatus.INTERRUPTED:
            return
        else:
            job.resolved = True
            self.queue_item.status = Status.INTERRUPTED
            self.queue_item.resolved = True
            self.worker.queue_stash.set_result(self.credentials, self.queue_item)
            self.worker.job_stash.set_result(self.credentials, job)
            process = psutil.Process(job.job_pid)
            process.terminate()

    def stop(self) -> None:
        self.stop_requested.set()


@serializable()
class QueueManager(BaseQueueManager):
    config: QueueConfig

    def post_init(self) -> None:
        self.client_config = self.config.client_config
        self._client = self.config.client_type(self.client_config)

    def close(self) -> SyftError | SyftSuccess:
        return self._client.close()

    def create_consumer(
        self,
        message_handler: type[AbstractMessageHandler],
        service_name: str,
        worker_stash: WorkerStash | None = None,
        address: str | None = None,
        syft_worker_id: UID | None = None,
    ) -> QueueConsumer:
        consumer = self._client.add_consumer(
            message_handler=message_handler,
            queue_name=message_handler.queue_name,
            address=address,
            service_name=service_name,
            worker_stash=worker_stash,
            syft_worker_id=syft_worker_id,
        )
        return consumer

    def create_producer(
        self,
        queue_name: str,
        queue_stash: type[BaseStash],
        context: AuthedServiceContext,
        worker_stash: WorkerStash,
    ) -> QueueProducer:
        return self._client.add_producer(
            queue_name=queue_name,
            queue_stash=queue_stash,
            context=context,
            worker_stash=worker_stash,
        )

    def send(
        self,
        message: bytes,
        queue_name: str,
    ) -> SyftSuccess | SyftError:
        return self._client.send_message(
            message=message,
            queue_name=queue_name,
        )

    @property
    def producers(self) -> Any:
        return self._client.producers

    @property
    def consumers(self) -> Any:
        return self._client.consumers


def handle_message_multiprocessing(
    worker_settings: WorkerSettings,
    queue_item: QueueItem,
    credentials: SyftVerifyKey,
) -> None:
    print(f"date: {datetime.now()} - message: handle_message_multiprocessing start")

    queue_config = worker_settings.queue_config
    if queue_config is None:
        raise ValueError(f"{worker_settings} has no queue configurations!")
    queue_config.client_config.create_producer = False
    queue_config.client_config.n_consumers = 0

    # relative
    from ...node.node import Node

    worker = Node(
        id=worker_settings.id,
        name=worker_settings.name,
        signing_key=worker_settings.signing_key,
        document_store_config=worker_settings.document_store_config,
        action_store_config=worker_settings.action_store_config,
        blob_storage_config=worker_settings.blob_store_config,
        queue_config=queue_config,
        is_subprocess=True,
        migrate=False,
    )

    job_item = worker.job_stash.get_by_uid(credentials, queue_item.job_id).ok()

    # Set monitor thread for this job.
    monitor_thread = MonitorThread(queue_item, worker, credentials)
    monitor_thread.start()

    if queue_item.service == "user":
        queue_item.service = "userservice"

    try:
        call_method = getattr(worker.get_service(queue_item.service), queue_item.method)

        role = worker.get_role_for_credentials(credentials=credentials)

        context = AuthedServiceContext(
            node=worker,
            credentials=credentials,
            role=role,
            job_id=queue_item.job_id,
            has_execute_permissions=queue_item.has_execute_permissions,
        )

        # relative
        from ...node.node import AuthNodeContextRegistry

        AuthNodeContextRegistry.set_node_context(
            node_uid=worker.id,
            context=context,
            user_verify_key=credentials,
        )
        print(f"date: {datetime.now()} - message: call_method start")

        result: Any = call_method(context, *queue_item.args, **queue_item.kwargs)
        print(f"date: {datetime.now()} - message: call_method end")

        status = Status.COMPLETED
        job_status = JobStatus.COMPLETED

        if isinstance(result, Ok):
            result = result.ok()
            if hasattr(result, "syft_action_data") and isinstance(
                result.syft_action_data, Err
            ):
                status = Status.ERRORED
                job_status = JobStatus.ERRORED
        elif isinstance(result, SyftError) or isinstance(result, Err):
            status = Status.ERRORED
            job_status = JobStatus.ERRORED

        else:
            raise Exception(f"Unknown result type: {type(result)}")
    except Exception as e:  # nosec
        status = Status.ERRORED
        job_status = JobStatus.ERRORED
        # stdlib

        raise e
        # result = SyftError(
        #     message=f"Failed with exception: {e}, {traceback.format_exc()}"
        # )
        # print("HAD AN ERROR WHILE HANDLING MESSAGE", result.message)

    queue_item.result = result
    queue_item.resolved = True
    queue_item.status = status

    # get new job item to get latest iter status
    job_item = worker.job_stash.get_by_uid(credentials, job_item.id).ok()

    # if result.is_ok():

    job_item.node_uid = worker.id
    job_item.result = result
    job_item.resolved = True
    job_item.status = job_status
    print(f"date: {datetime.now()} - message: set_result")

    worker.queue_stash.set_result(credentials, queue_item)
    worker.job_stash.set_result(credentials, job_item)
    print(f"date: {datetime.now()} - message: Finish monitor thread")

    # Finish monitor thread
    monitor_thread.stop()
    print(f"date: {datetime.now()} - message: Finish monitor thread end")


def evaluate_can_run_job(
    job_id: UID, job_stash: JobStash, credentials: SyftVerifyKey
) -> Result[Job, str]:
    """Evaluate if a Job can be executed by the user.

    A Job cannot be executed if any of the following are met:
    - User doesn't have permission to the job.
    - Job is either marked Completed or result is available.
    - Job is Cancelled or Interrupted.
    """
    res = job_stash.get_by_uid(credentials, job_id)

    # User doesn't have access to job
    if res.is_err():
        return res

    job_item = res.ok()

    if job_item.status == JobStatus.COMPLETED or job_item.resolved:
        return Err(f"Job: {job_id} already Completed.")
    elif job_item.status == JobStatus.INTERRUPTED:
        return Err(f"Job interrupted. Job Id: {job_id}")

    return Ok(job_item)


@serializable()
class APICallMessageHandler(AbstractMessageHandler):
    queue_name = "api_call"

    @staticmethod
    def handle_message(message: bytes, syft_worker_id: UID) -> None:
        print(f"date: {datetime.now()} - message: Node")
        # relative
        from ...node.node import Node

        queue_item = deserialize(message, from_bytes=True)
        worker_settings = queue_item.worker_settings

        queue_config = worker_settings.queue_config
        queue_config.client_config.create_producer = False
        queue_config.client_config.n_consumers = 0

        worker = Node(
            id=worker_settings.id,
            name=worker_settings.name,
            signing_key=worker_settings.signing_key,
            document_store_config=worker_settings.document_store_config,
            action_store_config=worker_settings.action_store_config,
            blob_storage_config=worker_settings.blob_store_config,
            queue_config=queue_config,
            is_subprocess=True,
            migrate=False,
        )

        # otherwise it reads it from env, resulting in the wrong credentials
        worker.id = worker_settings.id
        worker.signing_key = worker_settings.signing_key

        credentials = queue_item.syft_client_verify_key
        print(f"date: {datetime.now()} - message: evaluate_can_run_job")

        res = evaluate_can_run_job(queue_item.job_id, worker.job_stash, credentials)
        if res.is_err():
            raise Exception(res.value)
        job_item: Job = res.ok()

        queue_item.status = Status.PROCESSING
        queue_item.node_uid = worker.id

        job_item.status = JobStatus.PROCESSING
        job_item.node_uid = cast(UID, worker.id)
        job_item.updated_at = DateTime.now()

        if syft_worker_id is not None:
            job_item.job_worker_id = syft_worker_id

        queue_result = worker.queue_stash.set_result(credentials, queue_item)
        if isinstance(queue_result, SyftError):
            raise Exception(f"{queue_result.err()}")

        job_result = worker.job_stash.set_result(credentials, job_item)
        if isinstance(job_result, SyftError):
            raise Exception(f"{job_result.err()}")

        handle_message_multiprocessing(worker_settings, queue_item, credentials)
