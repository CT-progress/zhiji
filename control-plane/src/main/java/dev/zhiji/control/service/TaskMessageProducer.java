package dev.zhiji.control.service;

import dev.zhiji.control.config.RabbitMQConfig;
import dev.zhiji.control.model.TaskDispatch;
import dev.zhiji.control.model.TaskMessage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.core.MessagePostProcessor;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;

/**
 * Publishes task dispatch messages to RabbitMQ.
 *
 * <p>The controller calls this instead of dispatching to the worker directly,
 * decoupling task submission from execution. The message carries the trace ID
 * as a header for cross-process correlation.
 */
@Component
public class TaskMessageProducer {

    private static final Logger log = LoggerFactory.getLogger(TaskMessageProducer.class);

    private final RabbitTemplate rabbitTemplate;
    private final TaskMetrics metrics;

    public TaskMessageProducer(RabbitTemplate rabbitTemplate, TaskMetrics metrics) {
        this.rabbitTemplate = rabbitTemplate;
        this.metrics = metrics;
    }

    public void publish(TaskDispatch dispatch) {
        TaskMessage message = TaskMessage.from(dispatch);
        MessagePostProcessor headerInjector = msg -> {
            msg.getMessageProperties().setMessageId(dispatch.taskId());
            msg.getMessageProperties().setHeader("x-trace-id", dispatch.traceId());
            msg.getMessageProperties().setHeader("x-platform", dispatch.platform());
            return msg;
        };
        rabbitTemplate.convertAndSend(
            RabbitMQConfig.TASK_EXCHANGE,
            RabbitMQConfig.DISPATCH_ROUTING_KEY,
            message,
            headerInjector
        );
        metrics.incDispatched();
        log.info("published task {} to {} (trace={})",
            dispatch.taskId(), RabbitMQConfig.DISPATCH_QUEUE, dispatch.traceId());
    }
}
