package com.skyblock.sniper.client.gui;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.skyblock.sniper.client.AhSniperClient;
import net.fabricmc.api.EnvType;
import net.fabricmc.api.Environment;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;

import java.util.ArrayList;
import java.util.List;

@Environment(EnvType.CLIENT)
public class FlipScreen extends Screen {
    private static final int ROW_HEIGHT = 28;
    private static final int HEADER_HEIGHT = 45;

    public FlipScreen() {
        super(Component.translatable("gui.ah_sniper.title"));
    }

    @Override
    protected void init() {
        super.init();

        int y = HEADER_HEIGHT;
        int listWidth = Math.min(this.width - 40, 520);
        int startX = (this.width - listWidth) / 2;

        // Populate [Inspect] buttons for each active flip in the sliding window
        List<JsonObject> flips = new ArrayList<>(AhSniperClient.ACTIVE_FLIPS);
        for (int i = 0; i < flips.size(); i++) {
            if (y + ROW_HEIGHT > this.height - 35) {
                break; // Stop when reaching bottom of screen
            }

            JsonObject flip = flips.get(i);
            String uuid = flip.has("uuid") ? flip.get("uuid").getAsString() : "";
            int buttonWidth = 65;
            int buttonHeight = 20;
            int buttonX = startX + listWidth - buttonWidth;
            int buttonY = y + 2;

            if (!uuid.isEmpty()) {
                Button inspectBtn = Button.builder(Component.literal("Inspect"), b -> {
                    // Execute Hypixel native command: /viewauction <uuid>
                    if (this.minecraft != null && this.minecraft.player != null) {
                        this.minecraft.player.connection.sendCommand("viewauction " + uuid);
                    }
                    // Immediately close screen so Hypixel's AH window opens cleanly
                    this.onClose();
                }).bounds(buttonX, buttonY, buttonWidth, buttonHeight).build();

                this.addRenderableWidget(inspectBtn);
            }

            y += ROW_HEIGHT;
        }

        // Add bottom close button
        this.addRenderableWidget(Button.builder(Component.literal("Close"), b -> this.onClose())
                .bounds(this.width / 2 - 50, this.height - 28, 100, 20)
                .build());
    }

    @Override
    public void extractRenderState(GuiGraphicsExtractor extractor, int mouseX, int mouseY, float delta) {
        // Dark background
        this.extractTransparentBackground(extractor);

        // Title Header
        extractor.centeredText(this.font, this.title, this.width / 2, 12, 0xFFFFAA00);

        // Connection Status Indicator
        boolean connected = AhSniperClient.IS_CONNECTED.get();
        String statusText = connected ? "● Connected (ws://127.0.0.1:8765)" : "● Disconnected (Reconnecting...)";
        int statusColor = connected ? 0xFF55FF55 : 0xFFFF5555;
        extractor.centeredText(this.font, statusText, this.width / 2, 26, statusColor);

        // Render Flips List
        List<JsonObject> flips = new ArrayList<>(AhSniperClient.ACTIVE_FLIPS);
        if (flips.isEmpty()) {
            extractor.centeredText(this.font,
                    Component.translatable("gui.ah_sniper.no_flips"),
                    this.width / 2,
                    this.height / 2 - 10,
                    0xFFAAAAAA
            );
        } else {
            int y = HEADER_HEIGHT;
            int listWidth = Math.min(this.width - 40, 520);
            int startX = (this.width - listWidth) / 2;

            JsonObject hoveredFlip = null;

            for (JsonObject flip : flips) {
                if (y + ROW_HEIGHT > this.height - 35) {
                    break;
                }

                boolean isHovered = mouseX >= startX && mouseX <= startX + listWidth - 70 && mouseY >= y && mouseY <= y + ROW_HEIGHT - 2;
                if (isHovered) {
                    hoveredFlip = flip;
                }

                // Item Name
                String name = flip.has("name") ? flip.get("name").getAsString() : "Unknown Item";
                extractor.text(this.font, name, startX + 4, y + 4, 0xFFFFFFFF);

                // Pricing Details
                long buyPrice = flip.has("buy_price") ? flip.get("buy_price").getAsLong() : 0;
                long profit = flip.has("profit") ? flip.get("profit").getAsLong() : 0;
                double margin = flip.has("margin_pct") ? flip.get("margin_pct").getAsDouble() : 0.0;

                String buyFormatted = String.format("Buy: %,d coins", buyPrice);
                String profitFormatted = String.format("+%,d (%.1f%%)", profit, margin);

                extractor.text(this.font, buyFormatted, startX + 4, y + 15, 0xFFFFAA00);
                extractor.text(this.font, profitFormatted, startX + 160, y + 15, 0xFF55FF55);

                // Category badge if present
                if (flip.has("category") && !flip.get("category").getAsString().isEmpty()) {
                    String cat = "[" + flip.get("category").getAsString() + "]";
                    extractor.text(this.font, cat, startX + 280, y + 15, 0xFF55FFFF);
                }

                y += ROW_HEIGHT;
            }

            // Render calculation tooltip if row is hovered
            if (hoveredFlip != null && hoveredFlip.has("calculations")) {
                JsonArray calcs = hoveredFlip.getAsJsonArray("calculations");
                if (calcs != null && !calcs.isEmpty()) {
                    List<Component> tooltipLines = new ArrayList<>();
                    tooltipLines.add(Component.literal("§6§lAddon Breakdown:"));
                    for (JsonElement elem : calcs) {
                        tooltipLines.add(Component.literal("§7→ §f" + elem.getAsString()));
                    }
                    if (hoveredFlip.has("target_resell")) {
                        long resell = hoveredFlip.get("target_resell").getAsLong();
                        tooltipLines.add(Component.literal(String.format("§aTarget Resell: §e%,d", resell)));
                    }
                    extractor.setComponentTooltipForNextFrame(this.font, tooltipLines, mouseX, mouseY);
                }
            }
        }

        // Render interactive widgets (Buttons)
        super.extractRenderState(extractor, mouseX, mouseY, delta);
    }

    @Override
    public boolean isPauseScreen() {
        return false;
    }
}
