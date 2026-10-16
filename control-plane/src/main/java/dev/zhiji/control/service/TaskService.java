package dev.zhiji.control.service;

import dev.zhiji.control.model.TaskEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * In-memory task state tracker for phase 6.1.
 *
 * <p>Later iterations will persist task state to a database; for now we keep
 * an in-memory map keyed by taskId. The callback controller writes here,
 * the dispatch controller reads here.
 */
@Service
public class TaskService {

    private static final Logger log = LoggerFactory.getLogger(TaskService.class);

    public record TaskState(
        String taskId,
        String traceId,
        String userId,
        String status,
        int progress,
        String lastEvent,
        String error
    ) {
        public TaskState withEvent(TaskEvent event) {
            String newStatus = switch (event.eventType()) {
                case TaskEvent.TASK_STARTED -> "running";
                case TaskEvent.TASK_SUCCEEDED -> "succeeded";
                case TaskEvent.TASK_FAILED -> "failed";
                default -> this.status;
            };
            int newProgress = this.progress;
            String lastEvent = event.eventType();
            String error = this.error;
            if (event.data() != null) {
                Object p = event.data().get("progress");
                if (p instanceof Number n) {
                    newProgress = n.intValue();
                }
                Object e = event.data().get("error");
                if (e instanceof String s) {
                    error = s;
                }
            }
            return new TaskState(taskId, traceId, userId, newStatus, newProgress, lastEvent, error);
        }
    }

    private final Map<String, TaskState> tasks = new ConcurrentHashMap<>();

    public TaskState registerDispatch(String taskId, String traceId, String userId) {
        TaskState state = new TaskState(taskId, traceId, userId, "pending", 0, null, null);
        tasks.put(taskId, state);
        return state;
    }

    public TaskState registerDispatch(String taskId, String traceId) {
        return registerDispatch(taskId, traceId, null);
    }

    public TaskState get(String taskId) {
        return tasks.get(taskId);
    }

    public TaskState handleEvent(TaskEvent event) {
        TaskState current = tasks.get(event.taskId());
        if (current == null) {
            log.warn("received event for unknown task: {}", event.taskId());
            return null;
        }
        TaskState updated = current.withEvent(event);
        tasks.put(event.taskId(), updated);
        log.info("task {} -> {} (trace={})", event.taskId(), updated.status(), event.traceId());
        return updated;
    }

    public void clear(String taskId) {
        tasks.remove(taskId);
    }
}
