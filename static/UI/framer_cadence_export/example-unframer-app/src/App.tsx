import './framer/styles.css'

import TestimonialFramerComponent from './framer/cards/testimonial'
import FeaturesDesktopFramerComponent from './framer/features-desktop'
import PricingPlansFramerComponent from './framer/pricing-plans'
import AccordionFramerComponent from './framer/accordion'
import FeaturesMobileFramerComponent from './framer/features-mobile'
import CardFramerComponent from './framer/card'
import ButtonFramerComponent from './framer/button'

export default function App() {
  return (
    <div className='flex flex-col items-center gap-3 bg-[rgb(0,_0,_0)]'>
      <TestimonialFramerComponent.Responsive
        ECqJZ1hHM={"Freelance Designer"}
        VePkjXSEB={"\"Cadence completely changed how I work. I get more done in less time without feeling overwhelmed.\""}
        oRy6t91PU={"Emily R."}
      />
      <FeaturesDesktopFramerComponent.Responsive/>
      <PricingPlansFramerComponent.Responsive/>
      <AccordionFramerComponent.Responsive/>
      <FeaturesMobileFramerComponent.Responsive/>
      <CardFramerComponent.Responsive
        E5hH6Ww9k={"Instant Clarity"}
        oJ0nKFcw9={"Start each day with a clear, AI-powered plan, no guesswork needed."}
      />
      <ButtonFramerComponent.Responsive
        H9fdquxmO={"Try Cadence for free"}
        qZfHbmzQf={true}
        qa2d0SHwc={"https://framer.link/sbOJsNi"}
        uFNDrhDvW={true}
      />
    </div>
  );
};