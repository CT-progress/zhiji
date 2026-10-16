package dev.zhiji.control.controller;

import dev.zhiji.control.model.AckResponse;
import dev.zhiji.control.model.TaskEvent;
import dev.zhiji.control.service.TaskService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/**
 * Receives task event callbacks from the Python worker.
 *
 * <p>This is the callback side of the contract (worker -> control plane).
 * The worker POSTs events here as the pipeline progresses.
 */
@RestController
@RequestMapping("/internal/v1/tasks/{taskId}/events")
public class TaskCallbackController {

    private static final Logger log = LoggerFactory.getLogger(TaskCallbackController.class);

    private final TaskService taskService;

    public TaskCallbackController(TaskService taskService) {
        this.taskService = taskService;
    }

    @PostMapping
    public ResponseEntity<AckResponse> reportEvent(
        @PathVariable String taskId,
        @RequestBody TaskEvent event,
        @RequestHeader(value = "X-Trace-Id", required = false) String traceId
    ) {
        log.info("received callback: task={} event={} trace={}",
            taskId, event.eventType(), traceId);

        TaskService.TaskState updated = taskService.handleEvent(event);
        if (updated == null) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok(new AckResponse(true));
    }
}
