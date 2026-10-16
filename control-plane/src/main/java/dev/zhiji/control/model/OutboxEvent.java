package dev.zhiji.control.model;

import jakarta.persistence.*;
import java.time.Instant;

/**
 * Outbox event for the transactional outbox pattern.
 *
 * <p>Written in the same DB transaction as the audit log entry,
 * then asynchronously published to RabbitMQ by {@code OutboxPublisher}.
 */
@Entity
@Table(name = "outbox_event")
public class OutboxEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "event_time", nullable = false, updatable = false)
    private Instant eventTime;

    @Column(name = "aggregate_type", nullable = false, updatable = false, length = 16)
    private String aggregateType;

    @Column(name = "aggregate_id", updatable = false, length = 64)
    private String aggregateId;

    @Column(name = "event_type", nullable = false, updatable = false, length = 64)
    private String eventType;

    @Column(name = "payload", updatable = false, length = 4096)
    private String payload;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, updatable = true, length = 8)
    private OutboxStatus status;

    @Column(name = "published_at", updatable = true)
    private Instant publishedAt;

    protected OutboxEvent() {}

    public OutboxEvent(Instant eventTime, String aggregateType, String aggregateId,
                       String eventType, String payload, OutboxStatus status) {
        this.eventTime = eventTime;
        this.aggregateType = aggregateType;
        this.aggregateId = aggregateId;
        this.eventType = eventType;
        this.payload = payload;
        this.status = status;
    }

    public Long getId() { return id; }
    public Instant getEventTime() { return eventTime; }
    public String getAggregateType() { return aggregateType; }
    public String getAggregateId() { return aggregateId; }
    public String getEventType() { return eventType; }
    public String getPayload() { return payload; }
    public OutboxStatus getStatus() { return status; }
    public void setStatus(OutboxStatus status) { this.status = status; }
    public Instant getPublishedAt() { return publishedAt; }
    public void setPublishedAt(Instant publishedAt) { this.publishedAt = publishedAt; }
}
