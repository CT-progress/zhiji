package dev.zhiji.control.repository;

import dev.zhiji.control.model.OutboxEvent;
import dev.zhiji.control.model.OutboxStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * Repository for outbox events.
 */
@Repository
public interface OutboxEventRepository extends JpaRepository<OutboxEvent, Long> {

    List<OutboxEvent> findByStatusOrderByEventTimeAsc(OutboxStatus status);
}
