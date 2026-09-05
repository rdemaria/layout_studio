"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  digitIndexInDraft,
  initialDigitPlace,
  MAX_DIGIT_PLACE,
  MIN_DIGIT_PLACE,
  numberAtPlace,
  parseNumberDraft,
  stepNumberAtPlace,
} from "./number-input-value";

export function NumberInput({ value, onChange, label, min, step = "any" }: {
  value: number;
  onChange: (value: number) => void;
  label: string;
  min?: number;
  step?: number | "any";
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const groupRef = useRef<HTMLSpanElement>(null);
  const mirrorRef = useRef<HTMLSpanElement>(null);
  const digitRef = useRef<HTMLSpanElement>(null);
  const hintId = useId();
  const numericValue = Number.isFinite(value) ? value : 0;
  const [place, setPlace] = useState(() => initialDigitPlace(step));
  const [editing, setEditing] = useState<{ text: string; value: number } | null>(null);
  const currentValueRef = useRef(numericValue);
  // Preserve incomplete typing through our own model updates. A different
  // external value invalidates the draft immediately, before painting it.
  if (editing && editing.value !== numericValue) setEditing(null);
  const draft = editing?.value === numericValue ? editing.text : null;
  const formatted = numberAtPlace(numericValue, place);
  const text = draft ?? formatted.text;
  const digitIndex = draft === null ? formatted.digitIndex : digitIndexInDraft(draft, place);
  const amount = 10 ** place;

  useEffect(() => {
    currentValueRef.current = numericValue;
  }, [numericValue]);

  const emit = (next: number) => {
    currentValueRef.current = next;
    onChange(next);
  };

  const adjust = (direction: -1 | 1) => {
    const next = stepNumberAtPlace(currentValueRef.current, place, direction, min);
    setEditing(null);
    emit(next);
  };
  const adjustRef = useRef(adjust);
  useLayoutEffect(() => { adjustRef.current = adjust; });

  useEffect(() => {
    const group = groupRef.current;
    if (!group) return;
    const handleWheel = (event: WheelEvent) => {
      if (!group.contains(document.activeElement) || event.deltaY === 0
        || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      event.preventDefault();
      event.stopPropagation();
      adjustRef.current(event.deltaY < 0 ? 1 : -1);
    };
    group.addEventListener("wheel", handleWheel, { passive: false });
    return () => group.removeEventListener("wheel", handleWheel);
  }, []);

  const syncUnderlineScroll = () => {
    if (mirrorRef.current && inputRef.current) {
      mirrorRef.current.style.transform = `translateX(${-inputRef.current.scrollLeft}px)`;
    }
  };

  useLayoutEffect(() => {
    const input = inputRef.current;
    const digit = digitRef.current;
    if (input && digit && draft === null) {
      // Keep the selected digit visible when a narrow field or extra zeros scrolls.
      const left = digit.offsetLeft;
      const right = left + digit.offsetWidth;
      const visibleWidth = input.clientWidth - 10;
      if (left < input.scrollLeft) input.scrollLeft = left;
      else if (right > input.scrollLeft + visibleWidth) input.scrollLeft = right - visibleWidth;
    }
    syncUnderlineScroll();
  }, [draft, place, text]);

  const selectDigit = (direction: -1 | 1) => {
    setEditing(null);
    setPlace((current) => Math.max(MIN_DIGIT_PLACE, Math.min(MAX_DIGIT_PLACE, current + direction)));
  };

  const finishTyping = () => {
    if (draft !== null) {
      const parsed = parseNumberDraft(draft);
      if (parsed !== null) emit(Math.max(min ?? -Infinity, parsed));
      setEditing(null);
    }
  };

  return (
    <span className="number-control" ref={groupRef}>
      <span className="number-input-value">
        <Input
          ref={inputRef}
          aria-label={label}
          aria-describedby={hintId}
          aria-valuemin={min}
          aria-valuenow={numericValue}
          aria-valuetext={`${numericValue}; increment ${amount}`}
          className="number-input"
          type="text"
          inputMode="decimal"
          role="spinbutton"
          autoComplete="off"
          spellCheck={false}
          value={text}
          onScroll={syncUnderlineScroll}
          onBlur={finishTyping}
          onChange={(event) => {
            const nextDraft = event.target.value;
            const next = parseNumberDraft(nextDraft);
            const valid = next !== null && (min === undefined || next >= min);
            setEditing({ text: nextDraft, value: valid ? next : numericValue });
            if (valid) emit(next);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowUp" || event.key === "ArrowDown") {
              event.preventDefault();
              event.stopPropagation();
              adjust(event.key === "ArrowUp" ? 1 : -1);
            } else if (event.altKey && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
              event.preventDefault();
              event.stopPropagation();
              selectDigit(event.key === "ArrowLeft" ? 1 : -1);
            } else if (event.key === "Enter") {
              event.preventDefault();
              finishTyping();
            } else if (event.key === "Escape") {
              setEditing(null);
            }
          }}
        />
        <span className="number-input-underline" aria-hidden="true">
          <span ref={mirrorRef} className="number-input-mirror">
            {digitIndex < 0 ? text : <>
              {text.slice(0, digitIndex)}
              <span ref={digitRef} className="number-input-digit">{text[digitIndex]}</span>
              {text.slice(digitIndex + 1)}
            </>}
          </span>
        </span>
      </span>
      <span className="number-input-arrows">
        {([
          ["Increase", ChevronUp, 1],
          ["Decrease", ChevronDown, -1],
        ] as const).map(([action, Icon, direction]) => (
          <Button
            key={action} type="button" variant="ghost" size="icon-xs"
            aria-label={`${action} ${label} by ${amount}`} title={`${action} by ${amount}`}
            onPointerDown={(event) => event.preventDefault()}
            onClick={(event) => {
              event.preventDefault();
              inputRef.current?.focus({ preventScroll: true });
              adjust(direction);
            }}
          ><Icon /></Button>
        ))}
      </span>
      <span className="number-input-arrows number-input-digit-arrows">
        {([
          ["left", ChevronLeft, 1, "Coarser", place >= MAX_DIGIT_PLACE],
          ["right", ChevronRight, -1, "Finer", place <= MIN_DIGIT_PLACE],
        ] as const).map(([side, Icon, direction, description, disabled]) => (
          <Button
            key={side} type="button" variant="ghost" size="icon-xs" disabled={disabled}
            aria-label={`Select digit to the ${side} for ${label}`}
            title={`${description} digit (Alt+${side === "left" ? "Left" : "Right"})`}
            onPointerDown={(event) => event.preventDefault()}
            onClick={(event) => {
              event.preventDefault();
              inputRef.current?.focus({ preventScroll: true });
              selectDigit(direction);
            }}
          ><Icon /></Button>
        ))}
      </span>
      <span id={hintId} className="sr-only">
        The underlined digit changes with Up, Down, or the mouse wheel while focused.
        Select a coarser or finer digit with the left and right buttons or Alt+Left and Alt+Right.
      </span>
    </span>
  );
}
