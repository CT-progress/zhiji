package dev.zhiji.control.model;

import java.util.Map;

/**
 * Message payload published to RabbitMQ for asynchronous task dispatch.
 *
 * <p>Carries the same fields as {@link TaskDispatch} minus the schema version
 * (which is a contract concern, not a transport concern). The consumer
 * reconstructs a full {@code TaskDispatch} when calling the worker.
 */
public record TaskMessage(
    String taskId,
    String userId,
    String type,
    String platform,
    Map<String, Object> input,
    String traceId,
    int attempt,
    int priority
) {
    public static TaskMessage from(TaskDispatch dispatch) {
        return new TaskMessage(
            dispatch.taskId(),
            dispatch.userId(),
            dispatch.type(),
            dispatch.platform(),
            dispatch.input(),
            dispatch.traceId(),
            dispatch.attempt(),
            dispatch.priority()
        );
    }

    public TaskDispatch toDispatch() {
        return new TaskDispatch(
            TaskDispatch.SCHEMA_VERSION,
            taskId,
            userId,
            type,
            platform,
            input,
            traceId,
            attempt,
            priority
        );
    }
}
