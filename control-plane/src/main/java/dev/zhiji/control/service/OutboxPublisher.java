package dev.zhiji.control.service;

import dev.zhiji.control.config.RabbitMQConfig;
import dev.zhiji.control.model.OutboxEvent;
import dev.zhiji.control.model.OutboxStatus;
import dev.zhiji.control.repository.OutboxEventRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;

/**
 * Polls the outbox table and publishes pending events to RabbitMQ.
 *
 * <p>On success, the outbox row is marked SENT. On failure, it stays PENDING
 * and is retried on the next cycle.
 */
@Component
public class OutboxPublisher {

    private static final Logger log = LoggerFactory.getLogger(OutboxPublisher.class);

    private final OutboxEventRepository outboxEventRepository;
    private final RabbitTemplate rabbitTemplate;

    public OutboxPublisher(OutboxEventRepository outboxEventRepository,
                           RabbitTemplate rabbitTemplate) {
        this.outboxEventRepository = outboxEventRepository;
        this.rabbitTemplate = rabbitTemplate;
    }

    @Scheduled(fixedDelay = 5000)
    @Transactional
    public void publishPending() {
        List<OutboxEvent> pending = outboxEventRepository
            .findByStatusOrderByEventTimeAsc(OutboxStatus.PENDING);
        for (OutboxEvent event : pending) {
            try {
                rabbitTemplate.convertAndSend(
                    RabbitMQConfig.AUDIT_EXCHANGE,
                    RabbitMQConfig.AUDIT_ROUTING_KEY,
                    event.getPayload()
                );
                event.setStatus(OutboxStatus.SENT);
                event.setPublishedAt(Instant.now());
                outboxEventRepository.save(event);
                log.debug("published outbox event {} (type={})",
                    event.getId(), event.getEventType());
            } catch (Exception e) {
                log.warn("failed to publish outbox event {}: {}",
                    event.getId(), e.getMessage());
            }
        }
    }
}
