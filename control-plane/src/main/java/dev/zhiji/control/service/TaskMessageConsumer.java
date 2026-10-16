package dev.zhiji.control.service;

import dev.zhiji.control.config.RabbitMQConfig;
import dev.zhiji.control.client.WorkerClient;
import dev.zhiji.control.model.TaskMessage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

/**
 * Consumes task dispatch messages from RabbitMQ and forwards them to the
 * Python worker via HTTP.
 *
 * <p>Idempotency: before dispatching, checks {@link TaskService} for the
 * task state. If the task is already running or succeeded, the message is
 * silently consumed (no-op). This prevents duplicate task execution when
 * a message is redelivered after a transient failure.
 *
 * <p>DLQ handling: messages that fail after max retries are routed to the
 * dead-letter queue. The {@code handleDlq} listener logs the failure and
 * records a metric for monitoring.
 */
@Component
public class TaskMessageConsumer {

    private static final Logger log = LoggerFactory.getLogger(TaskMessageConsumer.class);

    private final WorkerClient workerClient;
    private final TaskService taskService;
    private final TaskMetrics metrics;

    public TaskMessageConsumer(WorkerClient workerClient, TaskService taskService, TaskMetrics metrics) {
        this.workerClient = workerClient;
        this.taskService = taskService;
        this.metrics = metrics;
    }

    @RabbitListener(queues = RabbitMQConfig.DISPATCH_QUEUE)
    public void consume(TaskMessage message) {
        TaskService.TaskState state = taskService.get(message.taskId());
        if (state != null && ("running".equals(state.status()) || "succeeded".equals(state.status()))) {
            log.info("skipping duplicate dispatch for task {} (status={})",
                message.taskId(), state.status());
            return;
        }

        log.info("consuming task {} from queue (trace={})",
            message.taskId(), message.traceId());
        try {
            workerClient.dispatch(message.toDispatch());
        } catch (Exception e) {
            metrics.incWorkerFailure();
            log.warn("worker dispatch failed for task {}: {}", message.taskId(), e.getMessage());
            throw e;
        }
    }

    @RabbitListener(queues = RabbitMQConfig.DLQ_QUEUE)
    public void handleDlq(TaskMessage message) {
        metrics.incDlq();
        log.error("task {} moved to DLQ after exhausting retries (trace={})",
            message.taskId(), message.traceId());
        if (taskService.get(message.taskId()) != null) {
            taskService.clear(message.taskId());
        }
    }
}
