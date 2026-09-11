(function (root, factory) {
  const position = factory();
  if (typeof module === "object" && module.exports) module.exports = position;
  else root.calculateSignalPopoverPosition = position;
})(typeof window !== "undefined" ? window : globalThis, function () {
  return function calculateSignalPopoverPosition(anchor, popover, viewport, options) {
    const margin = Number(options && options.margin) || 12;
    const gap = Number(options && options.gap) || 8;
    const viewportWidth = Math.max(0, Number(viewport && viewport.width) || 0);
    const viewportHeight = Math.max(0, Number(viewport && viewport.height) || 0);
    const width = Math.min(Math.max(0, Number(popover && popover.width) || 0), Math.max(0, viewportWidth - (margin * 2)));
    const height = Math.min(Math.max(0, Number(popover && popover.height) || 0), Math.max(0, viewportHeight - (margin * 2)));
    const anchorLeft = Number(anchor && anchor.left) || 0;
    const anchorTop = Number(anchor && anchor.top) || 0;
    const anchorWidth = Number(anchor && anchor.width) || 0;
    const anchorHeight = Number(anchor && anchor.height) || 0;
    const anchorRight = Number(anchor && anchor.right) || anchorLeft + anchorWidth;
    const anchorBottom = Number(anchor && anchor.bottom) || anchorTop + anchorHeight;
    const maxLeft = Math.max(margin, viewportWidth - width - margin);
    const preferredLeft = anchorRight - width - gap;
    const rightSideLeft = anchorLeft + anchorWidth + gap;
    const useRightSide = preferredLeft < margin && rightSideLeft + width <= viewportWidth - margin;
    const unclampedLeft = useRightSide ? rightSideLeft : preferredLeft;
    const left = Math.min(Math.max(margin, unclampedLeft), maxLeft);
    const below = anchorBottom + gap;
    const above = anchorTop - height - gap;
    const maxTop = Math.max(margin, viewportHeight - height - margin);
    let top = below;
    let vertical = "below";
    if (above >= margin) {
      top = above;
      vertical = "above";
    } else if (below + height > viewportHeight - margin) {
      top = Math.min(Math.max(margin, below), maxTop);
      vertical = "clamped";
    }
    let horizontal = useRightSide ? "right" : "left";
    if (unclampedLeft !== left) horizontal = "clamped";
    const placement = `${vertical}-${horizontal}`;
    return {
      left: Math.round(left),
      top: Math.round(Math.min(Math.max(margin, top), maxTop)),
      width: Math.round(width),
      maxHeight: Math.round(Math.max(0, viewportHeight - (margin * 2))),
      placement,
    };
  };
});
