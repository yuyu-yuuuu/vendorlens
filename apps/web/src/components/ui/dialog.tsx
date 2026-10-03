"use client";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="dialog-overlay" />
        <DialogPrimitive.Content
          className="dialog-content"
          onEscapeKeyDown={(event) => event.stopPropagation()}
        >
          <DialogPrimitive.Title className="dialog-title">
            {title}
          </DialogPrimitive.Title>
          <DialogPrimitive.Description
            className={description ? "muted" : "sr-only"}
          >
            {description ?? title}
          </DialogPrimitive.Description>
          <DialogPrimitive.Close
            className="icon-button dialog-close"
            aria-label="关闭"
          >
            <X size={20} />
          </DialogPrimitive.Close>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
