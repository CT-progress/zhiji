package dev.zhiji.control.service;

import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * In-memory token revocation (blocklist) service.
 *
 * <p>Stores revoked JWT IDs (jti) with their expiry time. Expired entries
 * are garbage-collected lazily on access. This is a stand-in for Redis
 * in phase 6.3.
 */
@Service
public class TokenRevocationService {

    private record RevokedEntry(Instant expiresAt) {}

    private final ConcurrentHashMap<String, RevokedEntry> revoked = new ConcurrentHashMap<>();

    public void revoke(String jti, Instant expiresAt) {
        revoked.put(jti, new RevokedEntry(expiresAt));
    }

    public boolean isRevoked(String jti) {
        if (jti == null) {
            return false;
        }
        RevokedEntry entry = revoked.get(jti);
        if (entry == null) {
            return false;
        }
        // Lazy GC: if the token has expired anyway, remove it
        if (entry.expiresAt().isBefore(Instant.now())) {
            revoked.remove(jti);
            return false;
        }
        return true;
    }

    /**
     * Remove all expired entries. Called periodically or on access.
     */
    public int cleanup() {
        Instant now = Instant.now();
        int removed = 0;
        var it = revoked.entrySet().iterator();
        while (it.hasNext()) {
            var entry = it.next();
            if (entry.getValue().expiresAt().isBefore(now)) {
                it.remove();
                removed++;
            }
        }
        return removed;
    }

    public int size() {
        return revoked.size();
    }

    public void clear() {
        revoked.clear();
    }
}
