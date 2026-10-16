package dev.zhiji.control.controller;

import dev.zhiji.control.client.WorkerClient;
import dev.zhiji.control.model.*;
import dev.zhiji.control.service.TaskService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;
import java.util.UUID;

/**
 * Task dispatch and query endpoints.
 *
 * <p>This is the control-plane-facing side: clients (web UI, admin) call these
 * to submit tasks. The control plane dispatches to the Python worker via
 * {@link WorkerClient} and tracks state via {@link TaskService}.
 */
@RestController
@RequestMapping("/api/v1/tasks")
public class TaskDispatchController {

    private final WorkerClient workerClient;
    private final TaskService taskService;

    public TaskDispatchController(WorkerClient workerClient, TaskService taskService) {
        this.workerClient = workerClient;
        this.taskService = taskService;
    }

    @PostMapping
    public ResponseEntity<DispatchResponse> dispatchTask(@RequestBody TaskDispatch request) {
        TaskDispatch dispatch = new TaskDispatch(
            TaskDispatch.SCHEMA_VERSION,
            request.taskId() != null ? request.taskId() : UUID.randomUUID().toString(),
            request.userId(),
            request.type(),
            request.platform(),
            request.input(),
            request.traceId() != null ? request.traceId() : UUID.randomUUID().toString(),
            request.attempt(),
            request.priority()
        );
        taskService.registerDispatch(dispatch.taskId(), dispatch.traceId());
        DispatchResponse resp = workerClient.dispatch(dispatch);
        if (resp == null) {
            return ResponseEntity.status(502).build();
        }
        return ResponseEntity.accepted().body(resp);
    }

    @GetMapping("/{taskId}")
    public ResponseEntity<TaskService.TaskState> getTask(@PathVariable String taskId) {
        TaskService.TaskState state = taskService.get(taskId);
        if (state == null) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok(state);
    }

    @PostMapping("/{taskId}/cancel")
    public ResponseEntity<CancelResponse> cancelTask(@PathVariable String taskId) {
        TaskService.TaskState state = taskService.get(taskId);
        if (state == null) {
            return ResponseEntity.notFound().build();
        }
        CancelResponse resp = workerClient.cancel(taskId, state.traceId());
        if (resp == null) {
            return ResponseEntity.status(502).build();
        }
        return ResponseEntity.ok(resp);
    }
}
