'use client';

import React, { useState } from 'react';
import { Navbar } from '@/components/Navbar';
import { Hero } from '@/components/Hero';
import { QuickAlertBar } from '@/components/QuickAlertBar';
import { ExtensionDemoWidget } from '@/components/ExtensionDemoWidget';
import { LiveDealsFeed } from '@/components/LiveDealsFeed';
import { AlertModal } from '@/components/AlertModal';
import { ExtensionAdvocateBanner } from '@/components/ExtensionAdvocateBanner';
import { Footer } from '@/components/Footer';

export default function HomePage() {
  const [isAlertModalOpen, setIsAlertModalOpen] = useState(false);
  const [initialAlertValue, setInitialAlertValue] = useState('');

  const handleOpenAlertModal = (initialAsinOrCategory?: string) => {
    setInitialAlertValue(initialAsinOrCategory || '');
    setIsAlertModalOpen(true);
  };

  return (
    <main className="min-h-screen flex flex-col justify-between">
      <Navbar onOpenAlertModal={() => handleOpenAlertModal()} />

      <div className="flex-1">
        {/* Hero Section */}
        <Hero onOpenAlertModal={() => handleOpenAlertModal()} />

        {/* Quick Alert Notifier Bar (e.g., hair pin -> alert) */}
        <QuickAlertBar />

        {/* Live Injected Extension Preview */}
        <ExtensionDemoWidget />

        {/* Live Deals Grid */}
        <LiveDealsFeed onOpenAlertModal={handleOpenAlertModal} />
      </div>

      <Footer />

      {/* Floating Extension Advocate Banner */}
      <ExtensionAdvocateBanner />

      {/* Triggerable Deal Alert Modal */}
      <AlertModal
        isOpen={isAlertModalOpen}
        onClose={() => setIsAlertModalOpen(false)}
        initialValue={initialAlertValue}
      />
    </main>
  );
}
