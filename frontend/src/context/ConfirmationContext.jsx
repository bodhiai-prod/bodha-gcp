import React, { createContext, useCallback, useContext, useRef, useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const ConfirmationContext = createContext(null);

export function ConfirmationProvider({ children }) {
  const [request, setRequest] = useState(null);
  const resolveRef = useRef(null);

  const settle = useCallback((confirmed) => {
    resolveRef.current?.(confirmed);
    resolveRef.current = null;
    setRequest(null);
  }, []);

  const confirm = useCallback((options) => new Promise((resolve) => {
    resolveRef.current = resolve;
    setRequest({
      title: "Please confirm",
      description: "Are you sure you want to continue?",
      confirmLabel: "Continue",
      cancelLabel: "Cancel",
      tone: "default",
      ...options,
    });
  }), []);

  return (
    <ConfirmationContext.Provider value={confirm}>
      {children}
      <AlertDialog
        open={Boolean(request)}
        onOpenChange={(open) => {
          if (!open && request) settle(false);
        }}
      >
        <AlertDialogContent className="w-[calc(100%-2rem)] max-w-md gap-5 rounded-none border-2 border-black bg-white p-6 hard-shadow-static">
          <AlertDialogHeader className="space-y-3 text-left">
            <AlertDialogTitle className="font-display text-3xl leading-none">
              {request?.title}
            </AlertDialogTitle>
            <AlertDialogDescription className="whitespace-pre-line text-sm leading-6 text-neutral-600">
              {request?.description}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="gap-3 sm:space-x-0">
            <AlertDialogCancel className="qp-btn qp-btn-secondary mt-0" onClick={() => settle(false)}>
              {request?.cancelLabel}
            </AlertDialogCancel>
            <AlertDialogAction
              className={`qp-btn ${request?.tone === "danger" ? "qp-btn-danger" : "qp-btn-primary"}`}
              onClick={() => settle(true)}
            >
              {request?.confirmLabel}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </ConfirmationContext.Provider>
  );
}

export function useConfirmation() {
  const confirm = useContext(ConfirmationContext);
  if (!confirm) {
    throw new Error("useConfirmation must be used within a ConfirmationProvider");
  }
  return confirm;
}
