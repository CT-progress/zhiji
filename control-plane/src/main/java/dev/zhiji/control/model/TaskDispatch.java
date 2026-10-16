package dev.zhiji.control.model;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.util.Map;
import java.util.UUID;

/**
 * Task dispatch request sent from the control plane to the Python worker.
 *
 * <p>Matches the {@code TaskDispatch} schema in {@code worker-api.yaml}.
 */
public record TaskDispatch(
    String schemaVersion,
    String taskId,
    String userId,
    String type,
    String platform,
    Map<String, Object> input,
    String traceId,
    int attempt,
    int priority
) {
    public static final String SCHEMA_VERSION = "1.0";

    public TaskDispatch {
        if (schemaVersion == null || schemaVersion.isBlank()) {
            schemaVersion = SCHEMA_VERSION;
        }
        if (taskId == null || taskId.isBlank()) {
            taskId = UUID.randomUUID().toString();
        }
        if (traceId == null || traceId.isBlank()) {
            traceId = UUID.randomUUID().toString();
        }
        if (attempt < 1) {
            attempt = 1;
        }
    }
}
