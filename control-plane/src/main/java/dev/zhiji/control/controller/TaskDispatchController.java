package dev.zhiji.control.controller;

import dev.zhiji.control.client.WorkerClient;
import dev.zhiji.control.model.*;
import dev.zhiji.control.service.RateLimiter;
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
 *
 * <p>Rate limiting is applied per-platform before dispatching to the
 * worker. When the QPS limit is exceeded, a 429 is returned without
 * contacting the worker.
 */
@RestController
@RequestMapping("/api/v1/tasks")
public class TaskDispatchController {

    private static final Logger log = LoggerFactory.getLogger(TaskDispatchController.class);

    private final WorkerClient workerClient;
    private final TaskService taskService;
    private final RateLimiter rateLimiter;

    public TaskDispatchController(WorkerClient workerClient, TaskService taskService, RateLimiter rateLimiter) {
        this.workerClient = workerClient;
        this.taskService = taskService;
        this.rateLimiter = rateLimiter;
    }

    @PostMapping
    public ResponseEntity<?> dispatchTask(@RequestBody TaskDispatch request) {
        // Per-platform rate limit check
        if (!rateLimiter.tryAcquire(request.platform())) {
            return ResponseEntity.status(429)
                .body(Map.of("error", "rate_limited", "platform", request.platform() != null ? request.platform() : "default"));
        }

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

        try {
            DispatchResponse resp = workerClient.dispatch(dispatch);
            return ResponseEntity.accepted().body(resp);
        } catch (Exception e) {
            log.warn("worker dispatch failed for task {}: {}", dispatch.taskId(), e.getMessage());
            return ResponseEntity.status(502)
                .body(Map.of("error", "worker_unavailable", "taskId", dispatch.taskId()));
        }
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
    public ResponseEntity<?> cancelTask(@PathVariable String taskId) {
        TaskService.TaskState state = taskService.get(taskId);
        if (state == null) {
            return ResponseEntity.notFound().build();
        }
        try {
            CancelResponse resp = workerClient.cancel(taskId, state.traceId());
            return ResponseEntity.ok(resp);
        } catch (Exception e) {
            log.warn("worker cancel failed for task {}: {}", taskId, e.getMessage());
            return ResponseEntity.status(502)
                .body(Map.of("error", "worker_unavailable", "taskId", taskId));
        }
    }

    private String currentUserId() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.getPrincipal() instanceof Jwt jwt) {
            return jwt.getSubject();
        }
        return "anonymous";
    }
}
