/* Extra interaction controls for the Layout Studio canvas viewer.
 *
 * Adds rectangle zoom, adjustable world-axis length, and canonical views
 * without coupling those controls to the editor module.
 */
(() => {
  "use strict";

  const M = globalThis.LayoutStudioModel;
  const namespace = globalThis.LayoutStudioViewer;
  const BaseViewer = namespace?.LayoutViewer;
  if (!M || !BaseViewer) throw new Error("model.js and viewer.js must load before viewer-controls.js");

  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  class LayoutViewerWithControls extends BaseViewer {
    constructor(canvas, options = {}) {
      super(canvas, options);
      this.axisScale = 1;
      this.zoomRectangle = null;
      this.controlDisposers = [];
      this.bindExtendedControls();
    }

    destroy() {
      for (const dispose of this.controlDisposers) dispose();
      this.controlDisposers = [];
      super.destroy();
    }

    bindExtendedControls() {
      const axisSlider = document.getElementById("axis-scale-slider");
      const axisValue = document.getElementById("axis-scale-value");
      if (axisSlider) {
        const updateAxis = () => {
          this.setAxisScale(Number(axisSlider.value));
          if (axisValue) axisValue.textContent = `${Math.round(this.axisScale * 100)}%`;
        };
        axisSlider.addEventListener("input", updateAxis);
        this.controlDisposers.push(() => axisSlider.removeEventListener("input", updateAxis));
        updateAxis();
      }

      for (const button of document.querySelectorAll("[data-standard-view]")) {
        const updateView = () => {
          this.setStandardView(button.dataset.standardView);
          button.closest("details")?.removeAttribute("open");
        };
        button.addEventListener("click", updateView);
        this.controlDisposers.push(() => button.removeEventListener("click", updateView));
      }
    }

    setMode(mode) {
      if (mode === "zoom") {
        this.mode = "zoom";
        this.zoomRectangle = null;
        this.updateCursor();
        this.requestDraw();
        return;
      }
      this.zoomRectangle = null;
      super.setMode(mode);
    }

    setAxisScale(value) {
      this.axisScale = clamp(Number.isFinite(value) ? value : 1, 0, 4);
      this.requestDraw();
    }

    setStandardView(view) {
      const epsilon = 0.025;
      const cameras = {
        "+x": { yaw: Math.PI / 2, pitch: 0 },
        "-x": { yaw: -Math.PI / 2, pitch: 0 },
        "+y": { yaw: 0, pitch: Math.PI / 2 - epsilon },
        "-y": { yaw: 0, pitch: -Math.PI / 2 + epsilon },
      };
      const camera = cameras[view];
      if (!camera) return;
      this.camera.yaw = camera.yaw;
      this.camera.pitch = camera.pitch;
      this.fit();
    }

    draw() {
      super.draw();
      if (!this.zoomRectangle) return;
      const { start, current } = this.zoomRectangle;
      const x = Math.min(start.x, current.x);
      const y = Math.min(start.y, current.y);
      const width = Math.abs(current.x - start.x);
      const height = Math.abs(current.y - start.y);
      const ctx = this.ctx;
      ctx.save();
      ctx.setTransform(this.pixelRatio, 0, 0, this.pixelRatio, 0, 0);
      ctx.fillStyle = "rgba(72, 167, 165, .14)";
      ctx.strokeStyle = "rgba(126, 226, 222, .95)";
      ctx.lineWidth = 1.25;
      ctx.setLineDash([5, 4]);
      ctx.fillRect(x, y, width, height);
      ctx.strokeRect(x + 0.5, y + 0.5, Math.max(0, width - 1), Math.max(0, height - 1));
      if (width >= 36 && height >= 22) {
        const label = `${Math.round(width)} × ${Math.round(height)} px`;
        ctx.setLineDash([]);
        ctx.font = "600 10px ui-monospace, SFMono-Regular, Consolas, monospace";
        const textWidth = ctx.measureText(label).width;
        ctx.fillStyle = "rgba(11, 26, 34, .9)";
        ctx.fillRect(x + 5, y + 5, textWidth + 10, 18);
        ctx.fillStyle = "#bdecea";
        ctx.fillText(label, x + 10, y + 18);
      }
      ctx.restore();
    }

    drawAxes(ctx) {
      const multiplier = this.axisScale ?? 1;
      if (multiplier <= 0) return;
      const boundsSize = this.scene.bounds ? Math.max(...this.scene.bounds.size, 1) : 5;
      const baseLength = Math.max(1, Math.min(boundsSize * 0.16, 10 / Math.max(this.camera.scale / 25, 0.2)));
      const length = baseLength * multiplier;
      const origin = this.project([0, 0, 0]);
      const axes = [
        { point: [length, 0, 0], color: "#e36b62", label: "X" },
        { point: [0, length, 0], color: "#6dcb8b", label: "Y" },
        { point: [0, 0, length], color: "#6da9e8", label: "Z" },
      ];
      ctx.save();
      ctx.font = "600 10px ui-monospace, SFMono-Regular, Consolas, monospace";
      for (const axis of axes) {
        const end = this.project(axis.point);
        ctx.strokeStyle = axis.color;
        ctx.fillStyle = axis.color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(origin.x, origin.y);
        ctx.lineTo(end.x, end.y);
        ctx.stroke();
        ctx.fillText(axis.label, end.x + 4, end.y - 3);
      }
      ctx.restore();
    }

    pointerDown(event) {
      if (this.mode === "zoom" && event.button === 0 && !event.shiftKey) {
        const point = this.localPointer(event);
        this.canvas.setPointerCapture(event.pointerId);
        this.pointer = {
          id: event.pointerId,
          start: point,
          last: point,
          button: event.button,
          action: "zoom-rectangle",
        };
        this.zoomRectangle = { start: point, current: point };
        this.dragDistance = 0;
        this.updateCursor(true);
        this.requestDraw();
        event.preventDefault();
        return;
      }
      if (this.mode === "zoom") {
        const mode = this.mode;
        this.mode = "pan";
        super.pointerDown(event);
        this.mode = mode;
        return;
      }
      super.pointerDown(event);
    }

    pointerMove(event) {
      if (this.pointer?.id === event.pointerId && this.pointer.action === "zoom-rectangle") {
        const point = this.localPointer(event);
        this.pointer.last = point;
        this.zoomRectangle.current = point;
        this.dragDistance = Math.hypot(point.x - this.pointer.start.x, point.y - this.pointer.start.y);
        this.requestDraw();
        event.preventDefault();
        return;
      }
      super.pointerMove(event);
    }

    pointerUp(event) {
      if (this.pointer?.id === event.pointerId && this.pointer.action === "zoom-rectangle") {
        const start = this.pointer.start;
        const end = this.localPointer(event);
        const cancelled = event.type === "pointercancel";
        this.pointer = null;
        this.zoomRectangle = null;
        try {
          this.canvas.releasePointerCapture(event.pointerId);
        } catch {
          // Pointer capture can already be gone after cancellation.
        }
        this.updateCursor(false);
        if (!cancelled && Math.abs(end.x - start.x) >= 8 && Math.abs(end.y - start.y) >= 8) {
          this.zoomToScreenRectangle(start, end);
        } else {
          this.requestDraw();
        }
        this.updateHover(end);
        event.preventDefault();
        return;
      }
      super.pointerUp(event);
    }

    zoomToScreenRectangle(start, end) {
      const x0 = clamp(Math.min(start.x, end.x), 0, this.cssWidth);
      const x1 = clamp(Math.max(start.x, end.x), 0, this.cssWidth);
      const y0 = clamp(Math.min(start.y, end.y), 0, this.cssHeight);
      const y1 = clamp(Math.max(start.y, end.y), 0, this.cssHeight);
      const width = Math.max(1, x1 - x0);
      const height = Math.max(1, y1 - y0);
      const centerX = (x0 + x1) / 2;
      const centerY = (y0 + y1) / 2;

      this.updateBasis();
      const dx = centerX - this.cssWidth / 2;
      const dy = centerY - this.cssHeight / 2;
      const shift = M.add(
        M.scale(this.basis.right, dx / this.camera.scale),
        M.scale(this.basis.up, -dy / this.camera.scale),
      );
      this.camera.target = M.add(this.camera.target, shift);
      const factor = 0.92 * Math.min(this.cssWidth / width, this.cssHeight / height);
      this.camera.scale = clamp(this.camera.scale * factor, 0.001, 100000);
      this.onViewChange({ ...this.camera });
      this.requestDraw();
    }

    updateCursor(dragging = false) {
      if (this.pointer?.action === "zoom-rectangle" || (!dragging && this.mode === "zoom")) {
        this.canvas.style.cursor = "crosshair";
        return;
      }
      super.updateCursor(dragging);
    }
  }

  globalThis.LayoutStudioViewer = Object.freeze({
    ...namespace,
    LayoutViewer: LayoutViewerWithControls,
  });
})();
