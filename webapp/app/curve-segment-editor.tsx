"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { NumberInput } from "./number-input";

export const SEGMENT_PAGE_SIZE = 50;

export function segmentPageRange(count: number, requestedPage: number) {
  const pageCount = Math.max(1, Math.ceil(count / SEGMENT_PAGE_SIZE));
  const page = Math.max(0, Math.min(pageCount - 1, requestedPage));
  return {
    page,
    pageCount,
    start: page * SEGMENT_PAGE_SIZE,
    end: Math.min(count, (page + 1) * SEGMENT_PAGE_SIZE),
  };
}

/** Never mount hidden rows, even while the enclosing collapsible animates. */
export function CurveSegmentEditor({
  open, curveName, segments, selectedIndex, page: requestedPage,
  onPageChange, onChange, onRemove, onAdd,
}: {
  open: boolean;
  curveName: string;
  segments: [number, number, number][];
  selectedIndex?: number;
  page: number;
  onPageChange: (page: number) => void;
  onChange: (index: number, axis: number, value: number) => void;
  onRemove: (index: number) => void;
  onAdd: () => void;
}) {
  if (!open) return null;
  const { page, pageCount, start, end } = segmentPageRange(segments.length, requestedPage);
  return <>
    {pageCount > 1 && (
      <div className="segment-pagination" aria-label="Segment pages">
        <Button type="button" variant="outline" size="xs" disabled={page === 0}
          onClick={() => onPageChange(page - 1)}>Previous</Button>
        <span>{start + 1}–{end} of {segments.length.toLocaleString("en-US")}</span>
        <Button type="button" variant="outline" size="xs" disabled={page + 1 === pageCount}
          onClick={() => onPageChange(page + 1)}>Next</Button>
      </div>
    )}
    <div className="segment-head">
      <span>#</span><span>Length [m]</span><span>Angle (°)</span><span>Roll (°)</span><span />
    </div>
    <div aria-label={`${curveName} segments`}
      className={`segment-list ${segments.length > 4 ? "segment-list-scrollable" : ""}`}>
      {segments.slice(start, end).map((segment, offset) => {
        const index = start + offset;
        return <div
          aria-label={`Segment ${index + 1} editor`}
          className={`segment-row ${selectedIndex === index ? "selected-segment-row" : ""}`}
          id={`curve-segment-row-${index}`} key={index} role="group" tabIndex={-1}>
          <span className="row-index">{String(index + 1).padStart(2, "0")}</span>
          {segment.map((value, axis) => (
            <NumberInput key={axis}
              value={axis === 0 ? value : Math.round(value * 180 / Math.PI * 1e10) / 1e10}
              min={axis === 0 ? 0 : undefined} step={axis === 0 ? 0.1 : 5}
              label={`Segment ${index + 1} ${["length", "angle", "roll"][axis]}`}
              onChange={(next) => onChange(index, axis,
                axis === 0 ? Math.max(0.000001, next) : next * Math.PI / 180)} />
          ))}
          <Button type="button" variant="ghost" size="icon-sm"
            aria-label={`Remove segment ${index + 1}`} disabled={segments.length === 1}
            onClick={() => onRemove(index)}><Trash2 /></Button>
        </div>;
      })}
    </div>
    <Button type="button" variant="outline" size="sm" className="wide-add" onClick={onAdd}>
      <Plus /> Add segment
    </Button>
  </>;
}
