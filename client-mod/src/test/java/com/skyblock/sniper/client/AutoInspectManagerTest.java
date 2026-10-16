package com.skyblock.sniper.client;

import com.skyblock.sniper.client.gui.FlipScreen;
import net.minecraft.network.chat.Component;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.lwjgl.glfw.GLFW;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.*;

public class AutoInspectManagerTest {

    private final List<String> dispatchedCommands = new ArrayList<>();
    private final List<Component> capturedActionBarMessages = new ArrayList<>();
    private final AtomicBoolean screenOpenState = new AtomicBoolean(false);

    @BeforeEach
    void setUp() {
        dispatchedCommands.clear();
        capturedActionBarMessages.clear();
        screenOpenState.set(false);

        AutoInspectManager.resetDefaults();
        AutoInspectManager.setCommandDispatcher(dispatchedCommands::add);
        AutoInspectManager.setActionBarNotifier(capturedActionBarMessages::add);
        AutoInspectManager.setScreenPredicate(screenOpenState::get);
        AutoInspectManager.setMainThreadExecutor(Runnable::run);
        FlipScreen.clearVanished();
    }

    @AfterEach
    void tearDown() {
        AutoInspectManager.resetDefaults();
        FlipScreen.clearVanished();
    }

    @Test
    void testToggleStateAndActionBarMessage() {
        assertFalse(AutoInspectManager.isEnabled());

        // Toggle ON
        AutoInspectManager.toggle();
        assertTrue(AutoInspectManager.isEnabled(), "Auto-Inspect must be enabled after first toggle");
        assertEquals(1, capturedActionBarMessages.size());

        String msg1 = capturedActionBarMessages.get(0).getString();
        assertTrue(msg1.contains("Auto-Inspect") && msg1.contains("ENABLED"),
                "Expected ENABLED in action bar message, got: " + msg1);

        // Toggle OFF
        AutoInspectManager.toggle();
        assertFalse(AutoInspectManager.isEnabled(), "Auto-Inspect must be disabled after second toggle");
        assertEquals(2, capturedActionBarMessages.size());

        String msg2 = capturedActionBarMessages.get(1).getString();
        assertTrue(msg2.contains("Auto-Inspect") && msg2.contains("DISABLED"),
                "Expected DISABLED in action bar message, got: " + msg2);
    }

    @Test
    void testProcessFlipDisabledDoesNothing() {
        AutoInspectManager.setEnabled(false);

        AutoInspectManager.processFlip("381fbc464161476db841f3e7fc71cfa0");
        assertTrue(dispatchedCommands.isEmpty(), "No command must be dispatched when Auto-Inspect is disabled");
    }

    @Test
    void testProcessFlipNullOrEmptyUuidDoesNothing() {
        AutoInspectManager.setEnabled(true);

        AutoInspectManager.processFlip(null);
        AutoInspectManager.processFlip("");
        AutoInspectManager.processFlip("   ");
        assertTrue(dispatchedCommands.isEmpty(), "No command must be dispatched for null or empty UUIDs");
    }

    @Test
    void testUuidSanitizationAndLengthValidation() {
        AutoInspectManager.setEnabled(true);
        screenOpenState.set(false);

        // Hyphenated UUID should be cleaned to 32 lowercase hex chars
        String hyphenated = "381fbc46-4161-476d-b841-f3e7fc71cfa0";
        AutoInspectManager.processFlip(hyphenated);
        assertEquals(1, dispatchedCommands.size());
        assertEquals("viewauction 381fbc464161476db841f3e7fc71cfa0", dispatchedCommands.get(0));

        // Invalid length UUID (<32 or >32) must be dropped
        AutoInspectManager.resetCooldown();
        AutoInspectManager.processFlip("short-uuid");
        AutoInspectManager.processFlip("toolong-1111222233334444555566667777888899990000");
        assertEquals(1, dispatchedCommands.size(), "Invalid length UUIDs must not be dispatched");
    }

    @Test
    void testLifoPriorityStackSelection() {
        AutoInspectManager.setEnabled(true);
        screenOpenState.set(false);

        // Put manager on cooldown temporarily to enqueue multiple snipes in a single tick
        AutoInspectManager.setLastExecutionTimeMs(System.currentTimeMillis());

        // Queue alert A: MEDIUM confidence, 5M profit
        AutoInspectManager.processFlip("11111111111111111111111111111111", 5_000_000, "MEDIUM");
        // Queue alert B: HIGH confidence, 2M profit
        AutoInspectManager.processFlip("22222222222222222222222222222222", 2_000_000, "HIGH");
        // Queue alert C: LOW confidence, 20M profit
        AutoInspectManager.processFlip("33333333333333333333333333333333", 20_000_000, "LOW");

        // Now expire cooldown and trigger processing
        AutoInspectManager.resetCooldown();
        AutoInspectManager.processFlip("44444444444444444444444444444444", 1_000_000, "LOW");

        // Out of all queued alerts, alert B (HIGH confidence) must win and be dispatched
        assertEquals(1, dispatchedCommands.size());
        assertEquals("viewauction 22222222222222222222222222222222", dispatchedCommands.get(0));
    }

    @Test
    void testTtlDiscardsStaleAlerts() {
        AutoInspectManager.setEnabled(true);
        screenOpenState.set(false);

        // Enqueue stale alert by simulating arrival 5,000ms ago (> 4,000ms TTL)
        AutoInspectManager.enqueueAlert(new AutoInspectManager.QueuedAlert(
                "11111111111111111111111111111111",
                "11111111111111111111111111111111",
                10_000_000,
                "HIGH",
                System.currentTimeMillis() - 5000L
        ));

        // Arrive with fresh alert (within cooldown = 0)
        AutoInspectManager.resetCooldown();
        AutoInspectManager.processFlip("22222222222222222222222222222222", 1_000_000, "MEDIUM");

        // Stale 1111... was older than TTL, so fresh 2222... must be selected
        assertEquals(1, dispatchedCommands.size());
        assertEquals("viewauction 22222222222222222222222222222222", dispatchedCommands.get(0));
    }

    @Test
    void testCooldownThrottlesRapidIncomingAlerts() {
        AutoInspectManager.setEnabled(true);
        screenOpenState.set(false);

        // First alert: dispatched immediately
        AutoInspectManager.processFlip("11111111111111111111111111111111");
        assertEquals(1, dispatchedCommands.size());
        assertEquals("viewauction 11111111111111111111111111111111", dispatchedCommands.get(0));

        // Second alert arrived within <500ms: throttled by 2000ms cooldown guard
        AutoInspectManager.processFlip("22222222222222222222222222222222");
        assertEquals(1, dispatchedCommands.size(), "Second rapid alert must be throttled by 2000ms cooldown");

        // Simulate 2001ms passing
        AutoInspectManager.setLastExecutionTimeMs(System.currentTimeMillis() - 2001L);

        // Third alert after cooldown: dispatched successfully
        AutoInspectManager.processFlip("33333333333333333333333333333333");
        assertEquals(2, dispatchedCommands.size());
        assertEquals("viewauction 33333333333333333333333333333333", dispatchedCommands.get(1));
    }

    @Test
    void testActiveScreenGuardPreventsInterruptingOpenContainer() {
        AutoInspectManager.setEnabled(true);
        screenOpenState.set(true); // Active chest/AH screen is open!

        AutoInspectManager.processFlip("11111111111111111111111111111111");

        // Verify command was NEVER dispatched because a container screen was open
        assertTrue(dispatchedCommands.isEmpty(),
                "Active screen guard must prevent dispatching command when a container is open");
    }

    @Test
    void testVanishedChatMessageInterception() {
        AutoInspectManager.setEnabled(true);
        screenOpenState.set(false);

        String uuid = "381fbc464161476db841f3e7fc71cfa0";
        AutoInspectManager.processFlip(uuid);
        assertEquals(1, dispatchedCommands.size());
        assertFalse(FlipScreen.isVanished(uuid));

        // Simulate chat response from server
        AutoInspectManager.onChatMessage("This auction wasn't found!");
        assertTrue(FlipScreen.isVanished(uuid), "Auction must be marked as vanished in FlipScreen");
    }

    @Test
    void testWebSocketClientHandlerParsesValidPayload() {
        AutoInspectManager.setEnabled(true);
        screenOpenState.set(false);

        // Malformed json or empty handling defensively
        assertDoesNotThrow(() -> WebSocketClientHandler.onMessage("not valid json"));
        assertDoesNotThrow(() -> WebSocketClientHandler.onMessage(null));
        assertDoesNotThrow(() -> WebSocketClientHandler.onMessage("{}"));

        // Valid json payload with 32-hex uuid triggers auto inspect
        WebSocketClientHandler.onMessage("{\"uuid\":\"11111111111111111111111111111111\",\"profit\":500000,\"confidence_label\":\"HIGH\"}");
        assertEquals(1, dispatchedCommands.size());
        assertEquals("viewauction 11111111111111111111111111111111", dispatchedCommands.get(0));
    }

    @Test
    void testModKeybindingsMetadata() {
        assertEquals(GLFW.GLFW_KEY_SEMICOLON, 59, "Semicolon keycode must be GLFW_KEY_SEMICOLON (59)");
    }

    @Test
    void testComAhSniperClientAliases() {
        assertFalse(com.ahsniper.client.AutoInspectManager.isEnabled());
        com.ahsniper.client.AutoInspectManager.toggle();
        assertTrue(com.ahsniper.client.AutoInspectManager.isEnabled());
    }
}
