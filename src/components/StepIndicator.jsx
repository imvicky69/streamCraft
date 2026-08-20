import React from 'react';
import { Check } from 'lucide-react';

export default function StepIndicator({ currentStep }) {
  const steps = [
    { number: 1, label: 'Paste URL' },
    { number: 2, label: 'Choose Quality' },
    { number: 3, label: 'Download' },
  ];

  return (
    <div className="step-indicator-container">
      {steps.map((step, index) => {
        const isCompleted = currentStep > step.number;
        const isActive = currentStep === step.number;

        return (
          <React.Fragment key={step.number}>
            <div className={`step-item ${isActive ? 'active' : ''} ${isCompleted ? 'completed' : ''}`}>
              <div className="step-number">
                {isCompleted ? <Check size={15} strokeWidth={3} /> : step.number}
              </div>
              <span className="step-label">{step.label}</span>
            </div>
            {index < steps.length - 1 && (
              <div className={`step-line ${currentStep > step.number ? 'filled' : ''}`} />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}
