package dev.zhiji.control.config;

import org.springframework.amqp.core.*;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * RabbitMQ topology: exchange, queues, dead-letter exchange, and bindings.
 *
 * <p>Layout:
 * <pre>
 *   zhiji.tasks (direct exchange)
 *     |-- zhiji.tasks.dispatch (queue, DLX = zhiji.tasks.dlx)
 *     |-- routing key: dispatch
 *
 *   zhiji.tasks.dlx (dead-letter direct exchange)
 *     |-- zhiji.tasks.dispatch.dlq (queue)
 *     |-- routing key: dispatch
 * </pre>
 *
 * <p>Messages that fail after max retries are routed to the DLQ for
 * inspection and compensation.
 */
@Configuration
public class RabbitMQConfig {

    public static final String TASK_EXCHANGE = "zhiji.tasks";
    public static final String DISPATCH_QUEUE = "zhiji.tasks.dispatch";
    public static final String DISPATCH_ROUTING_KEY = "dispatch";
    public static final String DLX_EXCHANGE = "zhiji.tasks.dlx";
    public static final String DLQ_QUEUE = "zhiji.tasks.dispatch.dlq";
    public static final String DLQ_ROUTING_KEY = "dispatch";

    @Bean
    public DirectExchange taskExchange() {
        return new DirectExchange(TASK_EXCHANGE, true, false);
    }

    @Bean
    public DirectExchange dlxExchange() {
        return new DirectExchange(DLX_EXCHANGE, true, false);
    }

    @Bean
    public Queue dispatchQueue() {
        return QueueBuilder.durable(DISPATCH_QUEUE)
            .withArgument("x-dead-letter-exchange", DLX_EXCHANGE)
            .withArgument("x-dead-letter-routing-key", DLQ_ROUTING_KEY)
            .build();
    }

    @Bean
    public Queue dlqQueue() {
        return QueueBuilder.durable(DLQ_QUEUE).build();
    }

    @Bean
    public Binding dispatchBinding(Queue dispatchQueue, DirectExchange taskExchange) {
        return BindingBuilder.bind(dispatchQueue)
            .to(taskExchange)
            .with(DISPATCH_ROUTING_KEY);
    }

    @Bean
    public Binding dlqBinding(Queue dlqQueue, DirectExchange dlxExchange) {
        return BindingBuilder.bind(dlqQueue)
            .to(dlxExchange)
            .with(DLQ_ROUTING_KEY);
    }

    @Bean
    public MessageConverter jackson2JsonMessageConverter() {
        return new Jackson2JsonMessageConverter();
    }
}
