"use client";

import { useId, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import type {
  LayoutData,
  ObjectPosition,
  Reference,
  Transformation,
  TransformName,
  TransformOperation,
} from "./layout-data";
import {
  NON_CURVE_TRANSFORM_NAMES,
  TRANSFORM_NAMES,
  objectFrameNames,
} from "./layout-data";

import { NumberInput } from "./number-input";
export { NumberInput } from "./number-input";

export function Field({
  label,
  children,
  wide = false,
}: {
  label: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <label className={`field ${wide ? "field-wide" : ""}`}>
      <span>{label}</span>
      {children}
    </label>
  );
}

const SEARCH_RESULT_LIMIT = 50;

export function NamePicker({
  label,
  names,
  value,
  onSelect,
  onRename,
}: {
  label: string;
  names: string[];
  value: string;
  onSelect: (name: string) => void;
  onRename?: (from: string, to: string) => void;
}) {
  const inputId = useId();
  const renameInputRef = useRef<HTMLInputElement>(null);
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState(value);
  const [renameError, setRenameError] = useState("");

  const startRename = () => {
    setRenameDraft(value);
    setRenameError("");
    setRenaming(true);
    requestAnimationFrame(() => {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    });
  };

  const cancelRename = () => {
    setRenameDraft(value);
    setRenameError("");
    setRenaming(false);
  };

  const commitRename = () => {
    const next = renameDraft.trim();
    if (!next) {
      setRenameError(`${label} cannot be empty.`);
      return;
    }
    if (next !== value && names.includes(next)) {
      setRenameError(`${next} already exists.`);
      return;
    }
    onRename?.(value, next);
    setRenaming(false);
  };

  return (
    <div className="field field-wide name-picker">
      <span id={`${inputId}-label`}>{renaming ? `Rename ${label.toLowerCase()}` : label}</span>
      {renaming ? (
        <>
          <div className="name-picker-row">
            <Input
              ref={renameInputRef}
              aria-labelledby={`${inputId}-label`}
              aria-invalid={Boolean(renameError)}
              value={renameDraft}
              onChange={(event) => {
                setRenameDraft(event.target.value);
                setRenameError("");
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") commitRename();
                if (event.key === "Escape") cancelRename();
              }}
            />
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label={`Save ${label.toLowerCase()}`}
              title="Save name"
              onClick={commitRename}
            >
              <Check />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Cancel renaming ${label.toLowerCase()}`}
              title="Cancel"
              onClick={cancelRename}
            >
              <X />
            </Button>
          </div>
          {renameError && <span className="name-picker-error" role="alert">{renameError}</span>}
        </>
      ) : (
        <div className="name-picker-row">
          <Combobox<string>
            value={value}
            items={names}
            limit={SEARCH_RESULT_LIMIT}
            autoHighlight
            onValueChange={(next) => {
              if (typeof next === "string" && names.includes(next)) {
                onSelect(next);
              }
            }}
          >
            <ComboboxInput
              id={inputId}
              className="name-combobox"
              aria-labelledby={`${inputId}-label`}
              autoComplete="off"
              onFocus={(event) => event.currentTarget.select()}
            />
            <ComboboxContent>
              <ComboboxEmpty>No matching names.</ComboboxEmpty>
              <ComboboxList>
                {(name: string) => (
                  <ComboboxItem key={name} value={name}>
                    {name}
                  </ComboboxItem>
                )}
              </ComboboxList>
              {names.length > SEARCH_RESULT_LIMIT && (
                <div className="name-picker-count">
                  Showing up to {SEARCH_RESULT_LIMIT} matches — keep typing
                </div>
              )}
            </ComboboxContent>
          </Combobox>
          {onRename && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Rename ${label.toLowerCase()} ${value}`}
              title="Rename selected name"
              onClick={startRename}
            >
              <Pencil />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

export function OperationsEditor<TName extends TransformName>({
  value,
  onChange,
  allowedNames,
}: {
  value: [TName, number][];
  onChange: (value: [TName, number][]) => void;
  allowedNames: readonly TName[];
}) {
  const operationsRef = useRef<HTMLDivElement>(null);
  const [reorderMessage, setReorderMessage] = useState("");
  const toDegrees = (radians: number) =>
    Math.round((radians * 180 / Math.PI) * 1e10) / 1e10;

  const moveOperation = (index: number, direction: -1 | 1) => {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= value.length) return;
    const operations = [...value];
    const name = operations[index][0];
    [operations[index], operations[nextIndex]] = [
      operations[nextIndex],
      operations[index],
    ];
    onChange(operations);
    setReorderMessage(
      `${name} moved to position ${nextIndex + 1} of ${operations.length}`,
    );
    requestAnimationFrame(() => {
      const row = operationsRef.current?.querySelector(
        `[data-operation-index="${nextIndex}"]`,
      );
      const preferred = row?.querySelector<HTMLButtonElement>(
        `[data-move="${direction < 0 ? "up" : "down"}"]:not(:disabled)`,
      );
      const fallback = row?.querySelector<HTMLButtonElement>(
        "[data-move]:not(:disabled)",
      );
      (preferred ?? fallback)?.focus();
    });
  };

  return (
    <div className="operations-list" ref={operationsRef}>
      <ol className="operation-sequence">
        {value.map(([name, amount], index) => {
          const isRotation = name.startsWith("r");
          return (
            <li
              className="operation-row"
              data-operation-index={index}
              key={`${index}-${name}`}
            >
              <div
                className="operation-order-controls"
                role="group"
                aria-label={`Reorder operation ${index + 1}`}
              >
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  data-move="up"
                  aria-label={`Move ${name} up`}
                  title="Move operation up"
                  disabled={index === 0}
                  onClick={() => moveOperation(index, -1)}
                >
                  <ArrowUp />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  data-move="down"
                  aria-label={`Move ${name} down`}
                  title="Move operation down"
                  disabled={index === value.length - 1}
                  onClick={() => moveOperation(index, 1)}
                >
                  <ArrowDown />
                </Button>
              </div>
              <NativeSelect
                value={name}
                aria-label={`Operation ${index + 1}`}
                onChange={(event) => {
                  const operations = [...value];
                  operations[index] = [
                    event.target.value as TName,
                    amount,
                  ];
                  onChange(operations);
                }}
              >
                {allowedNames.map((entry) => (
                  <NativeSelectOption key={entry} value={entry}>
                    {entry}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
              <NumberInput
                value={isRotation ? toDegrees(amount) : amount}
                step={isRotation ? 5 : 0.1}
                label={`${name} [${isRotation ? "degrees" : "metres"}]`}
                onChange={(next) => {
                  const operations = [...value];
                  operations[index] = [
                    name,
                    isRotation ? next * Math.PI / 180 : next,
                  ];
                  onChange(operations);
                }}
              />
              <span className="operation-unit" aria-hidden="true">
                {isRotation ? "degree" : "m"}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove ${name}`}
                onClick={() =>
                  onChange(value.filter((_, item) => item !== index))
                }
              >
                <Trash2 />
              </Button>
            </li>
          );
        })}
      </ol>
      <span className="sr-only" aria-live="polite">
        {reorderMessage}
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="add-operation"
        onClick={() => onChange([...value, ["tx" as TName, 0]])}
      >
        <Plus /> Add operation
      </Button>
    </div>
  );
}

export function ReferenceEditor({
  value,
  layout,
  owner,
  onChange,
}: {
  value: Transformation & Partial<Pick<ObjectPosition, "reference_curve">>;
  layout: LayoutData;
  owner: { kind: "curve" | "object"; name: string };
  onChange: (
    value: Transformation & Partial<Pick<ObjectPosition, "reference_curve">>,
  ) => void;
}) {
  const reverseDependencies = new Map<string, string[]>();
  const addDependency = (node: string, transformation: Transformation) => {
    const reference = transformation.reference;
    const dependencies = [
      reference.kind === "curve"
        ? `curve:${reference.curve}`
        : reference.kind === "object_frame"
          ? `object:${reference.object}`
          : null,
    ];
    const objectPosition = transformation as Partial<ObjectPosition>;
    if (
      reference.kind !== "curve" &&
      objectPosition.reference_curve &&
      transformation.transformation.some(([name]) => name === "ts")
    ) {
      dependencies.push(`curve:${objectPosition.reference_curve}`);
    }
    for (const dependency of dependencies) {
      if (!dependency) continue;
      reverseDependencies.set(dependency, [
        ...(reverseDependencies.get(dependency) ?? []),
        node,
      ]);
    }
  };
  for (const [name, curve] of Object.entries(layout.reference_curves)) {
    addDependency(`curve:${name}`, curve.starting_frame);
  }
  for (const [name, object] of Object.entries(layout.objects)) {
    addDependency(`object:${name}`, object.position);
  }
  const unsafeReferences = new Set<string>();
  const pending = [`${owner.kind}:${owner.name}`];
  while (pending.length) {
    const node = pending.pop()!;
    if (unsafeReferences.has(node)) continue;
    unsafeReferences.add(node);
    pending.push(...(reverseDependencies.get(node) ?? []));
  }

  const curveNames = Object.keys(layout.reference_curves).filter(
    (name) => !unsafeReferences.has(`curve:${name}`),
  );
  const objectNames = Object.keys(layout.objects).filter(
    (name) => !unsafeReferences.has(`object:${name}`),
  );
  const frameNamesForObject = (objectName: string) => {
    const type = layout.types[layout.objects[objectName]?.type];
    return type ? objectFrameNames(type, layout.objects[objectName]) : ["center"];
  };
  const reference = value.reference;
  const referenceCurve = owner.kind === "object" ? value.reference_curve : undefined;
  const hasPathLookup = value.transformation.some(([name]) => name === "ts");
  const operationsWithoutPathLookup = value.transformation.map(
    ([name, amount]): TransformOperation => [name === "ts" ? "tt" : name, amount],
  );

  const emit = ({
    reference: nextReference = reference,
    transformation = value.transformation,
    referenceCurve: requestedReferenceCurve,
  }: {
    reference?: Reference;
    transformation?: TransformOperation[];
    referenceCurve?: string | null;
  }) => {
    const nextReferenceCurve = requestedReferenceCurve === undefined
      ? referenceCurve
      : requestedReferenceCurve ?? undefined;
    const next = {
      ...value,
      reference: nextReference,
      transformation,
    };
    delete next.reference_curve;
    if (owner.kind === "object" && nextReference.kind !== "curve" && nextReferenceCurve) {
      next.reference_curve = nextReferenceCurve;
    }
    onChange(next);
  };

  const setKind = (kind: Reference["kind"]) => {
    if (kind === "world") {
      const carriedCurve = reference.kind === "curve"
        ? reference.curve
        : referenceCurve;
      emit({
        reference: { kind: "world" },
        referenceCurve: carriedCurve,
        transformation:
          owner.kind === "object" && carriedCurve
            ? value.transformation
            : operationsWithoutPathLookup,
      });
    } else if (kind === "curve") {
      if (!curveNames.length) return;
      const curve = referenceCurve && curveNames.includes(referenceCurve)
        ? referenceCurve
        : curveNames[0] ?? "curve";
      emit({
        reference: { kind: "curve", curve },
        referenceCurve: undefined,
      });
    } else {
      if (!objectNames.length) return;
      const object = objectNames[0];
      const frame = frameNamesForObject(object)[0];
      const carriedCurve = reference.kind === "curve"
        ? reference.curve
        : referenceCurve;
      emit({
        reference: { kind: "object_frame", object, frame },
        referenceCurve: carriedCurve,
        transformation:
          owner.kind === "object" && carriedCurve
            ? value.transformation
            : operationsWithoutPathLookup,
      });
    }
  };

  return (
    <div className="transformation-editor">
      <div className="reference-row">
        <Field label="Reference">
          <NativeSelect
            value={reference.kind}
            onChange={(event) => setKind(event.target.value as Reference["kind"])}
          >
            <NativeSelectOption value="world">World</NativeSelectOption>
            <NativeSelectOption value="curve" disabled={!curveNames.length}>
              Curve
            </NativeSelectOption>
            <NativeSelectOption value="object_frame" disabled={!objectNames.length}>
              Object frame
            </NativeSelectOption>
          </NativeSelect>
        </Field>

        {reference.kind === "curve" && (
          <Field label="Curve">
            <NativeSelect
              value={reference.curve}
              onChange={(event) =>
                emit({
                  reference: { kind: "curve", curve: event.target.value },
                })
              }
            >
              {curveNames.map((name) => (
                <NativeSelectOption key={name} value={name}>{name}</NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
        )}

        {reference.kind === "object_frame" && (
          <>
            <Field label="Object">
              <NativeSelect
                value={reference.object}
                onChange={(event) => {
                  const object = event.target.value;
                  const frame = frameNamesForObject(object)[0];
                  emit({
                    reference: { kind: "object_frame", object, frame },
                  });
                }}
              >
                {objectNames.map((name) => (
                  <NativeSelectOption key={name} value={name}>{name}</NativeSelectOption>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Frame">
              <NativeSelect
                value={reference.frame}
                onChange={(event) =>
                  emit({
                    reference: { ...reference, frame: event.target.value },
                  })
                }
              >
                {frameNamesForObject(reference.object).map((name) => (
                  <NativeSelectOption key={name} value={name}>{name}</NativeSelectOption>
                ))}
              </NativeSelect>
            </Field>
          </>
        )}

        {owner.kind === "object" && reference.kind !== "curve" && (
          <Field label="Reference curve for ts">
            <NativeSelect
              value={referenceCurve ?? ""}
              onChange={(event) =>
                emit({ referenceCurve: event.target.value || null })
              }
            >
              <NativeSelectOption value="" disabled={hasPathLookup}>
                None — ts unavailable
              </NativeSelectOption>
              {curveNames.map((name) => (
                <NativeSelectOption key={name} value={name}>{name}</NativeSelectOption>
              ))}
            </NativeSelect>
          </Field>
        )}
      </div>

      {owner.kind === "object" && reference.kind !== "curve" && (
        <p className="reference-curve-help">
          ts first finds the unique curve plane containing the referenced frame origin.
        </p>
      )}

      <OperationsEditor
        value={value.transformation}
        allowedNames={
          reference.kind === "curve" || referenceCurve
            ? TRANSFORM_NAMES
            : NON_CURVE_TRANSFORM_NAMES
        }
        onChange={(transformation) => emit({ transformation })}
      />
    </div>
  );
}
