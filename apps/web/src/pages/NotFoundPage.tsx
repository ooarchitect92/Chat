import { ArrowLeft, Compass } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Brand } from '@/components/brand';
import { Button } from '@/components/ui';

export function NotFoundPage() { return <div className="not-found"><Brand/><span><Compass/></span><strong>404</strong><h1>That page is off the map.</h1><p>The link may be outdated, or this page may have moved.</p><Link to="/"><Button icon={ArrowLeft}>Back to overview</Button></Link></div>; }
