import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
const variants = cva("button", {
  variants: {
    variant: { default: "primary", outline: "outline", ghost: "ghost" },
    size: { default: "", sm: "small" },
  },
  defaultVariants: { variant: "default", size: "default" },
});
export function Button({
  className,
  variant,
  size,
  type = "button",
  ...props
}: React.ComponentProps<"button"> & VariantProps<typeof variants>) {
  return (
    <button
      type={type}
      className={cn(variants({ variant, size }), className)}
      {...props}
    />
  );
}
