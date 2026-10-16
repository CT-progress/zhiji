package dev.zhiji.control.model;

import com.fasterxml.jackson.annotation.JsonFormat;
import java.time.Instant;
import java.util.Map;

/**
 * Event callback from the Python worker to the control plane.
 *
 * <p>Matches the {@code TaskEvent} schema in {@code worker-api.yaml}.
 */
public record TaskEvent(
    String schemaVersion,
    String taskId,
    String eventType,
    String traceId,
    @JsonFormat(shape = JsonFormat.Shape.STRING) Instant timestamp,
    Map<String, Object> data
) {
    public static final String SCHEMA_VERSION = "1.0";

    // Event type constants matching the OpenAPI enum
    public static final String TASK_STARTED = "task.started";
    public static final String STEP_PROGRESS = "step.progress";
    public static final String STEP_SUCCEEDED = "step.succeeded";
    public static final String STEP_FAILED = "step.failed";
    public static final String TASK_SUCCEEDED = "task.succeeded";
    public static final String TASK_FAILED = "task.failed";
}
