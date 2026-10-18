"""评估任务提交（WC4.2）：``EvaluationService.run`` 作为后台任务执行。"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Depends

from api.deps import ServiceContainer, get_container
from api.errors import ApiError
from api.routes.tasks import get_task_manager
from api.schemas.common import ErrorResponse
from api.schemas.tasks import EvaluationRequest, TaskModel, TaskSubmitted
from api.tasks import ACTIVE_STATUSES, TaskContext, TaskManager
from observability.dashboard.services.evaluation_service import EvaluationService

__all__ = ["get_evaluation_service", "router"]

router = APIRouter(tags=["evaluation"])

EVALUATION_KIND = "evaluation"
_submit_lock = threading.Lock()


def get_evaluation_service(container: ServiceContainer = Depends(get_container)) -> EvaluationService:
    return container.evaluation


@router.post(
    "/evaluations",
    status_code=202,
    response_model=TaskSubmitted,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Submit an evaluation task",
)
def submit_evaluation(
    body: EvaluationRequest,
    evaluation: EvaluationService = Depends(get_evaluation_service),
    manager: TaskManager = Depends(get_task_manager),
) -> TaskSubmitted:
    test_set = body.test_set_path or str(evaluation.default_test_set_path())
    # WP2：只预检显式路径——默认 golden set 缺失是服务端配置问题，留给任务失败时报错。
    # 预检先于 409 活跃任务检查（422 优先），且不产生任务记录。
    if body.test_set_path and not evaluation.test_set_exists(body.test_set_path):
        raise ApiError(
            422,
            f"Test set path does not exist: {body.test_set_path}",
            details=[
                {
                    "loc": ["body", "test_set_path"],
                    "msg": f"test set not found: {body.test_set_path}",
                    "type": "path_not_found",
                }
            ],
        )
    params = {
        "test_set_path": test_set,
        "backend": body.backend,
        "top_k": body.top_k,
        "filters": body.filters,
    }

    def run(ctx: TaskContext) -> dict[str, Any]:
        report = evaluation.run(test_set, backend=body.backend, top_k=body.top_k, filters=body.filters)
        return report.to_dict()

    with _submit_lock:
        active, _ = manager.list(kind=EVALUATION_KIND, statuses=ACTIVE_STATUSES, limit=1)
        if active:
            raise ApiError(
                409,
                "An evaluation task is already active",
                details={"task_id": active[0].task_id},
            )
        record = manager.submit(EVALUATION_KIND, run, detail={"params": params})
    return TaskSubmitted(task_id=record.task_id, status=record.status, task=TaskModel(**record.to_dict()))
