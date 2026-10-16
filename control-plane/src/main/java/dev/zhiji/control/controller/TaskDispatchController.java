package dev.zhiji.control.controller;

import dev.zhiji.control.client.WorkerClient;
import dev.zhiji.control.model.*;
import dev.zhiji.control.service.TaskService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

import java.util.Map;
import java.util.UUID;

/**
 * Task dispatch and query endpoints.
 *
 * <p>Client-facing (web UI, admin). Requires JWT authentication.
 * The authenticated user's ID is propagated as the task's userId,
 * linking tasks to their owner for data isolation.
 */
@RestController
@RequestMapping("/api/v1/tasks")
public class TaskDispatchController {

    private static final Logger log = LoggerFactory.getLogger(TaskDispatchController.class);

    private final WorkerClient workerClient;
    private final TaskService taskService;

    public TaskDispatchController(WorkerClient workerClient, TaskService taskService) {
        this.workerClient = workerClient;
        this.taskService = taskService;
    }

    @PostMapping
    public ResponseEntity<DispatchResponse> dispatchTask(@RequestBody TaskDispatch request) {
        String userId = currentUserId();
        TaskDispatch dispatch = new TaskDispatch(
            TaskDispatch.SCHEMA_VERSION,
            request.taskId() != null ? request.taskId() : UUID.randomUUID().toString(),
            userId,
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

    private String currentUserId() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.getPrincipal() instanceof Jwt jwt) {
            return jwt.getSubject();
        }
        return "anonymous";
    }
}
