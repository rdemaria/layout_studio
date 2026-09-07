"use client";

import { useMemo } from "react";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Field, NamePicker, OperationsEditor } from "./layout-controls";
import { LOCAL_TRANSFORM_NAMES, objectFrameNames, type LayoutData, type LocalTransformation, type PlacementReference } from "./layout-data";

export function FeaturePlacementEditor({layout, value, frameNames, frameName, onChange}: {
  layout: LayoutData;
  value: LocalTransformation;
  frameNames: string[];
  frameName: string;
  onChange: (value: LocalTransformation) => void;
}) {
  const objectNames = useMemo(() => Object.keys(layout.objects), [layout.objects]);
  const curveNames = useMemo(() => Object.keys(layout.reference_curves), [layout.reference_curves]);
  const localNames = frameNames.filter((name) => name !== frameName);
  const reference = value.reference;
  const kind = reference?.kind ?? "anchor";
  const setReference = (next?: PlacementReference) => {
    const { reference: previous, ...placement } = value;
    void previous;
    onChange({...placement, ...(next ? {reference: next} : {})});
  };
  return <div className="transformation-editor" aria-label={`${frameName} placement`}>
    <div className="reference-row">
      <Field label="Reference">
        <NativeSelect value={kind} aria-label={`${frameName} reference`} onChange={(event) => {
          const next = event.target.value;
          if (next === "anchor") setReference();
          else if (next === "local_frame" && localNames.length) setReference({kind: "local_frame", frame: localNames[0]});
          else if (next === "world") setReference({kind: "world"});
          else if (next === "curve" && curveNames.length) setReference({kind: "curve", curve: curveNames[0]});
          else if (next === "object_frame" && objectNames.length) setReference({kind: "object_frame", object: objectNames[0], frame: "anchor"});
        }}>
          <NativeSelectOption value="anchor">Anchor (default)</NativeSelectOption>
          <NativeSelectOption value="local_frame" disabled={!localNames.length}>Local frame</NativeSelectOption>
          <NativeSelectOption value="world">World</NativeSelectOption>
          <NativeSelectOption value="curve" disabled={!curveNames.length}>Curve</NativeSelectOption>
          <NativeSelectOption value="object_frame" disabled={!objectNames.length}>Object frame</NativeSelectOption>
        </NativeSelect>
      </Field>
      {reference?.kind === "local_frame" && kind !== "anchor" && <NamePicker
        label="Local frame" names={localNames} value={reference.frame}
        onSelect={(frame) => setReference({kind: "local_frame", frame})} />}
      {reference?.kind === "curve" && <NamePicker label="Curve" names={curveNames} value={reference.curve}
        onSelect={(curve) => setReference({kind: "curve", curve})} />}
      {reference?.kind === "object_frame" && <>
        <NamePicker label="Object" names={objectNames} value={reference.object}
          onSelect={(object) => setReference({kind: "object_frame", object, frame: "anchor"})} />
        <NamePicker label="Frame" value={reference.frame}
          names={layout.objects[reference.object] ? objectFrameNames(layout.types[layout.objects[reference.object].type], layout.objects[reference.object]) : []}
          onSelect={(frame) => setReference({...reference, frame})} />
      </>}
    </div>
    <OperationsEditor value={value.transformation} allowedNames={LOCAL_TRANSFORM_NAMES}
      onChange={(transformation) => onChange({...value, transformation})} />
  </div>;
}
