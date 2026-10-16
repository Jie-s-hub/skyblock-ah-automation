package com.skyblock.sniper.client;

import com.skyblock.sniper.client.gui.FlipScreen;
import net.minecraft.ChatFormatting;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.Component;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.Executor;

public class AutoInspectManager {
    private static volatile boolean enabled = false;
    private static volatile long lastExecutionTimeMs = 0L;
    public static final long COOLDOWN_MS = 2000L;
    public static final long TTL_MS = 4000L;

    public static class QueuedAlert implements Comparable<QueuedAlert> {
        public final String rawUuid;
        public final String sanitizedUuid;
        public final double profit;
        public final String confidence;
        public final long timestampMs;

        public QueuedAlert(String rawUuid, String sanitizedUuid, double profit, String confidence, long timestampMs) {
            this.rawUuid = rawUuid;
            this.sanitizedUuid = sanitizedUuid;
            this.profit = profit;
            this.confidence = confidence != null ? confidence : "MEDIUM";
            this.timestampMs = timestampMs;
        }

        public int getConfidenceRank() {
            if ("HIGH".equalsIgnoreCase(confidence)) return 3;
            if ("MEDIUM".equalsIgnoreCase(confidence)) return 2;
            if ("LOW".equalsIgnoreCase(confidence)) return 1;
            return 0;
        }

        @Override
        public int compareTo(QueuedAlert other) {
            // Priority: Higher confidence first, then higher profit
            int confComp = Integer.compare(other.getConfidenceRank(), this.getConfidenceRank());
            if (confComp != 0) return confComp;
            return Double.compare(other.profit, this.profit);
        }
    }

    private static final Deque<QueuedAlert> alertStack = new ArrayDeque<>();
    private static final Object stackLock = new Object();
    private static volatile String lastInspectedUuid = null;
    private static volatile boolean strictUuidValidation = true;

    @FunctionalInterface
    public interface CommandDispatcher {
        void dispatch(String command);
    }

    @FunctionalInterface
    public interface ScreenPredicate {
        boolean isScreenOpen();
    }

    @FunctionalInterface
    public interface ActionBarNotifier {
        void notify(Component message);
    }

    // Lazy bridge to Minecraft client methods to prevent early classloading issues in headless/test environments
    private static class MinecraftBridge {
        static void sendCommand(String command) {
            Minecraft client = Minecraft.getInstance();
            if (client != null && client.getConnection() != null) {
                client.getConnection().sendCommand(command);
            }
        }

        static boolean isScreenOpen() {
            Minecraft client = Minecraft.getInstance();
            return client != null && client.screen != null;
        }

        static void sendOverlay(Component message) {
            Minecraft client = Minecraft.getInstance();
            if (client != null && client.player != null) {
                client.player.sendOverlayMessage(message);
            }
        }

        static void executeOnMainThread(Runnable task) {
            Minecraft client = Minecraft.getInstance();
            if (client != null) {
                client.execute(task);
            } else {
                task.run();
            }
        }
    }

    private static CommandDispatcher commandDispatcher = MinecraftBridge::sendCommand;
    private static ScreenPredicate screenPredicate = MinecraftBridge::isScreenOpen;
    private static ActionBarNotifier actionBarNotifier = MinecraftBridge::sendOverlay;
    private static Executor mainThreadExecutor = MinecraftBridge::executeOnMainThread;

    public static void setCommandDispatcher(CommandDispatcher dispatcher) {
        commandDispatcher = dispatcher != null ? dispatcher : MinecraftBridge::sendCommand;
    }

    public static void setScreenPredicate(ScreenPredicate predicate) {
        screenPredicate = predicate != null ? predicate : MinecraftBridge::isScreenOpen;
    }

    public static void setActionBarNotifier(ActionBarNotifier notifier) {
        actionBarNotifier = notifier != null ? notifier : MinecraftBridge::sendOverlay;
    }

    public static void setMainThreadExecutor(Executor executor) {
        mainThreadExecutor = executor != null ? executor : MinecraftBridge::executeOnMainThread;
    }

    public static void setStrictUuidValidation(boolean strict) {
        strictUuidValidation = strict;
    }

    public static boolean isStrictUuidValidation() {
        return strictUuidValidation;
    }

    public static String getLastInspectedUuid() {
        return lastInspectedUuid;
    }

    public static void resetDefaults() {
        commandDispatcher = MinecraftBridge::sendCommand;
        screenPredicate = MinecraftBridge::isScreenOpen;
        actionBarNotifier = MinecraftBridge::sendOverlay;
        mainThreadExecutor = MinecraftBridge::executeOnMainThread;
        enabled = false;
        lastExecutionTimeMs = 0L;
        lastInspectedUuid = null;
        strictUuidValidation = true;
        synchronized (stackLock) {
            alertStack.clear();
        }
    }

    public static void toggle() {
        toggle(null);
    }

    public static void toggle(Minecraft client) {
        enabled = !enabled;
        ChatFormatting statusColor = enabled ? ChatFormatting.GREEN : ChatFormatting.RED;
        String statusText = enabled ? "ENABLED" : "DISABLED";

        Component message = Component.literal("[AH Sniper] Auto-Inspect: ")
                .withStyle(ChatFormatting.GOLD)
                .append(Component.literal(statusText).withStyle(statusColor, ChatFormatting.BOLD));

        // Render on in-game action bar (overlay message)
        if (actionBarNotifier != null) {
            actionBarNotifier.notify(message);
        } else if (client != null && client.player != null) {
            client.player.sendOverlayMessage(message);
        }
    }

    public static boolean isEnabled() {
        return enabled;
    }

    public static void setEnabled(boolean value) {
        enabled = value;
    }

    public static long getLastExecutionTimeMs() {
        return lastExecutionTimeMs;
    }

    public static void setLastExecutionTimeMs(long timeMs) {
        lastExecutionTimeMs = timeMs;
    }

    public static void resetCooldown() {
        lastExecutionTimeMs = 0L;
    }

    public static String sanitizeUuid(String rawUuid) {
        if (rawUuid == null) return "";
        return rawUuid.replace("-", "").replaceAll("[^a-fA-F0-9]", "").toLowerCase().trim();
    }

    public static boolean isValidUuid(String cleanUuid) {
        if (cleanUuid == null || cleanUuid.isEmpty()) return false;
        if (!strictUuidValidation) return true;
        return cleanUuid.length() == 32;
    }

    public static void onChatMessage(String text) {
        if (text == null) return;
        if (text.contains("This auction wasn't found!") || text.contains("You cannot view this auction!")) {
            if (lastInspectedUuid != null) {
                FlipScreen.markVanished(lastInspectedUuid);
            }
        }
    }

    public static void enqueueAlert(QueuedAlert alert) {
        if (alert == null) return;
        synchronized (stackLock) {
            alertStack.push(alert);
        }
    }

    public static void handleIncomingFlip(String auctionUuid) {
        processFlip(auctionUuid, 0.0, "MEDIUM");
    }

    public static void handleIncomingFlip(String auctionUuid, double profit, String confidence) {
        processFlip(auctionUuid, profit, confidence);
    }

    public static void processFlip(String auctionUuid) {
        processFlip(auctionUuid, 0.0, "MEDIUM");
    }

    public static void processFlip(Minecraft client, String auctionUuid) {
        processFlip(auctionUuid, 0.0, "MEDIUM");
    }

    public static void processFlip(String auctionUuid, double profit, String confidence) {
        if (!enabled || auctionUuid == null || auctionUuid.trim().isEmpty()) {
            return;
        }

        String cleanUuid = sanitizeUuid(auctionUuid);
        if (!isValidUuid(cleanUuid)) {
            return;
        }

        long now = System.currentTimeMillis();

        synchronized (stackLock) {
            alertStack.push(new QueuedAlert(auctionUuid, cleanUuid, profit, confidence, now));
        }

        if (now - lastExecutionTimeMs < COOLDOWN_MS) {
            return;
        }

        Runnable task = () -> {
            if (!enabled) {
                return;
            }

            long current = System.currentTimeMillis();
            if (current - lastExecutionTimeMs < COOLDOWN_MS) {
                return;
            }

            // Container / Active Screen Guard:
            // Do not override or kick the player out if an AH/chest/inventory screen is already open
            if (screenPredicate != null && screenPredicate.isScreenOpen()) {
                return;
            }

            QueuedAlert bestAlert = null;
            synchronized (stackLock) {
                List<QueuedAlert> validAlerts = new ArrayList<>();
                while (!alertStack.isEmpty()) {
                    QueuedAlert alert = alertStack.pop();
                    if (current - alert.timestampMs <= TTL_MS) {
                        validAlerts.add(alert);
                    }
                }
                if (validAlerts.isEmpty()) {
                    return;
                }

                // Sort LIFO stack by priority (Confidence HIGH > MEDIUM > LOW, then profit desc)
                Collections.sort(validAlerts);
                bestAlert = validAlerts.get(0);

                // Discard stale queue after picking the highest-confidence/highest-profit alert
                alertStack.clear();
            }

            if (bestAlert != null) {
                lastExecutionTimeMs = current;
                lastInspectedUuid = bestAlert.sanitizedUuid;
                if (commandDispatcher != null) {
                    commandDispatcher.dispatch("viewauction " + bestAlert.sanitizedUuid);
                }
            }
        };

        if (mainThreadExecutor != null) {
            mainThreadExecutor.execute(task);
        } else {
            MinecraftBridge.executeOnMainThread(task);
        }
    }
}
