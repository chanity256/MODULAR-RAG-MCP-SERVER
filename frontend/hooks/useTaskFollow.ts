"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toApiError, type ApiError } from "@/lib/errors";
import { followTask, isTerminalStatus, type TaskFollower, type TaskRecord, type TaskTransport } from "@/lib/tasks";

export interface TaskFollowState {
  taskId: string | null;
  task: TaskRecord | null;
  transport: TaskTransport | null;
  /** Failure to observe the task (e.g. 401/404); a task that itself failed is `task.status`. */
  error: ApiError | null;
  following: boolean;
}

const IDLE: TaskFollowState = { taskId: null, task: null, transport: null, error: null, following: false };

/**
 * Follow one background task at a time with `followTask` (WS, polling fall-back). Starting
 * a new follow, calling `clear`, or unmounting stops the previous one. `onFinished` fires
 * once when the followed task reaches a terminal status.
 */
export function useTaskFollow(onFinished?: (task: TaskRecord) => void) {
  const [state, setState] = useState<TaskFollowState>(IDLE);
  const followerRef = useRef<TaskFollower | null>(null);
  const finishedRef = useRef(onFinished);

  useEffect(() => {
    finishedRef.current = onFinished;
  });

  const seqRef = useRef(0);

  const stop = useCallback(() => {
    seqRef.current += 1;
    followerRef.current?.stop();
    followerRef.current = null;
  }, []);

  const follow = useCallback(
    (taskId: string, initial?: TaskRecord) => {
      stop();
      const seq = ++seqRef.current;
      const current = () => seqRef.current === seq;
      setState({ taskId, task: initial ?? null, transport: null, error: null, following: true });
      const follower = followTask(taskId, {
        onUpdate: (task) => {
          if (current()) setState((prev) => ({ ...prev, task, error: null }));
        },
        onTransportChange: (transport) => {
          if (current()) setState((prev) => ({ ...prev, transport }));
        },
      });
      followerRef.current = follower;
      follower.done.then(
        (task) => {
          if (!current()) return;
          followerRef.current = null;
          setState((prev) => ({ ...prev, following: false }));
          if (task && isTerminalStatus(task.status)) finishedRef.current?.(task);
        },
        (error: unknown) => {
          if (!current()) return;
          followerRef.current = null;
          setState((prev) => ({ ...prev, following: false, error: toApiError(error) }));
        },
      );
    },
    [stop],
  );

  const clear = useCallback(() => {
    stop();
    setState(IDLE);
  }, [stop]);

  useEffect(() => stop, [stop]);

  const { taskId, task } = state;
  const retry = useCallback(() => {
    if (taskId) follow(taskId, task ?? undefined);
  }, [follow, taskId, task]);

  return { ...state, follow, clear, retry };
}
