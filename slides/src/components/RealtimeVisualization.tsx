import { useState, useEffect, useRef } from 'react';
import { Button } from './ui/button';
import { Card } from './ui/card';

interface SensorData {
  yawThigh: number;
  yawShin: number;
  timestamp: number;
}

const RealtimeVisualization = () => {
  const [sensorData, setSensorData] = useState<SensorData>({ yawThigh: 0, yawShin: 0, timestamp: 0 });
  const [isConnected, setIsConnected] = useState(false);
  const [hasCalibrated, setHasCalibrated] = useState(false);
  const [refThigh, setRefThigh] = useState(0);
  const [refShin, setRefShin] = useState(0);
  const [refDelta, setRefDelta] = useState(0);
  
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const ws = new WebSocket('ws://localhost:3002');
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('Connected to sensor stream');
      setIsConnected(true);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setSensorData(data);
      } catch (err) {
        console.error('Error parsing sensor data:', err);
      }
    };

    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      setIsConnected(false);
    };

    ws.onclose = () => {
      console.log('Disconnected from sensor stream');
      setIsConnected(false);
    };

    return () => {
      ws.close();
    };
  }, []);

  const handleCalibrate = () => {
    setRefThigh(sensorData.yawThigh);
    setRefShin(sensorData.yawShin);
    setRefDelta(sensorData.yawShin - sensorData.yawThigh);
    setHasCalibrated(true);
    console.log('Calibrated:', { refThigh: sensorData.yawThigh, refShin: sensorData.yawShin });
  };

  const relThigh = hasCalibrated ? sensorData.yawThigh - refThigh : 0;
  const rawDelta = sensorData.yawShin - sensorData.yawThigh;
  let kneeAngle = hasCalibrated ? rawDelta - refDelta : 0;
  
  kneeAngle = ((kneeAngle + 180) % 360 + 360) % 360 - 180;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    if (!hasCalibrated) {
      ctx.fillStyle = '#fff';
      ctx.font = '16px sans-serif';
      ctx.fillText('Press "Calibrate" with leg extended', 20, canvas.height / 2);
      return;
    }

    const thighRad = -relThigh * Math.PI / 180;
    const kneeRadDraw = -kneeAngle * Math.PI / 180;
    const kneeRadArc = kneeAngle * Math.PI / 180;

    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    const thighLen = 150;
    const shankLen = 140;

    ctx.save();
    ctx.translate(centerX, centerY);

    ctx.rotate(thighRad);

    ctx.strokeStyle = '#b4b4b4';
    ctx.lineWidth = 14;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(0, -thighLen);
    ctx.stroke();

    ctx.fillStyle = '#c8c8c8';
    ctx.beginPath();
    ctx.arc(0, -thighLen, 13, 0, 2 * Math.PI);
    ctx.fill();

    ctx.fillStyle = '#78dcff';
    ctx.fillRect(-11, -thighLen / 2 - 6, 22, 12);

    ctx.fillStyle = '#ffc800';
    ctx.beginPath();
    ctx.arc(0, 0, 15, 0, 2 * Math.PI);
    ctx.fill();

    ctx.save();
    ctx.rotate(kneeRadDraw);

    ctx.strokeStyle = '#64c8ff';
    ctx.lineWidth = 14;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(0, shankLen);
    ctx.stroke();

    ctx.fillStyle = '#78dcff';
    ctx.fillRect(-11, shankLen / 2 - 6, 22, 12);

    ctx.fillStyle = '#c8c8c8';
    ctx.beginPath();
    ctx.arc(0, shankLen, 11, 0, 2 * Math.PI);
    ctx.fill();

    ctx.save();
    ctx.translate(0, shankLen);
    
    const footLength = 60;
    const footHeight = 20;
    
    ctx.fillStyle = '#9ca3af';
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(-footLength, 0);
    ctx.lineTo(-footLength, footHeight);
    ctx.lineTo(0, footHeight);
    ctx.closePath();
    ctx.fill();
    
    ctx.beginPath();
    ctx.arc(-footLength, footHeight / 2, footHeight / 2, Math.PI / 2, -Math.PI / 2);
    ctx.fill();
    
    ctx.strokeStyle = '#6b7280';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(-footLength, 0);
    ctx.arc(-footLength, footHeight / 2, footHeight / 2, -Math.PI / 2, Math.PI / 2, true);
    ctx.lineTo(0, footHeight);
    ctx.closePath();
    ctx.stroke();
    
    ctx.restore();

    ctx.restore();

    const thighDir = -Math.PI / 2;
    const shankDir = Math.atan2(
      Math.cos(kneeRadDraw) * shankLen,
      Math.sin(kneeRadDraw) * shankLen
    );

    const rawArc = shankDir - thighDir;
    const arcAngle = Math.atan2(Math.sin(rawArc), Math.cos(rawArc));

    const arcRadius = 50;
    const absArc = Math.abs(arcAngle);

    if (absArc > 0.05) {
      ctx.strokeStyle = '#00ff88';
      ctx.lineWidth = 2.5;
      ctx.setLineDash([5, 5]);

      const startAngle = thighDir;
      const endAngle = thighDir - arcAngle;

      ctx.beginPath();
      ctx.arc(0, 0, arcRadius, startAngle, endAngle, arcAngle > 0);
      ctx.stroke();
      ctx.setLineDash([]);

      const midAngle = startAngle - arcAngle / 2;
      const labelRadius = arcRadius + 25;
      const labelX = Math.cos(midAngle) * labelRadius;
      const labelY = Math.sin(midAngle) * labelRadius;

      ctx.fillStyle = '#00ff88';
      ctx.font = 'bold 16px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(`${Math.abs(180-kneeAngle).toFixed(1)}°`, labelX, labelY);
    }

    ctx.restore();

  }, [sensorData, hasCalibrated, relThigh, kneeAngle, refThigh, refShin, refDelta]);

  return (
    <div className="flex flex-col items-center gap-4 p-6">
      <Card className="p-6 w-full max-w-4xl">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-2xl font-bold">Real-time Leg Tracking</h2>
          <div className="flex gap-2 items-center">
            <div className={`w-3 h-3 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`} />
            <span className="text-sm">{isConnected ? 'Connected' : 'Disconnected'}</span>
          </div>
        </div>

        <canvas 
          ref={canvasRef}
          width={800}
          height={400}
          className="w-full rounded-lg border border-primary/30 bg-[hsl(var(--background))]"
        />

        <div className="mt-4 grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <p className="text-sm font-mono">
              <span className="text-muted-foreground">Thigh Yaw (rel):</span>{' '}
              <span className="text-secondary">{relThigh.toFixed(2)}°</span>
            </p>
            <p className="text-sm font-mono">
              <span className="text-muted-foreground">Knee Angle:</span>{' '}
              <span className="text-green-400">{kneeAngle.toFixed(2)}°</span>
            </p>
          </div>
          <div className="space-y-2">
            <p className="text-sm font-mono">
              <span className="text-muted-foreground">Thigh (cont):</span>{' '}
              {sensorData.yawThigh.toFixed(2)}°
            </p>
            <p className="text-sm font-mono">
              <span className="text-muted-foreground">Shin (cont):</span>{' '}
              {sensorData.yawShin.toFixed(2)}°
            </p>
          </div>
        </div>

        <div className="mt-4 flex gap-2">
          <Button 
            onClick={handleCalibrate}
            disabled={!isConnected}
            className="w-full"
          >
            Calibrate (Press with leg extended)
          </Button>
        </div>

        {!isConnected && (
          <div className="mt-4 p-4 bg-yellow-500/10 border border-yellow-500/50 rounded-lg">
            <p className="text-sm text-yellow-200">
              Not connected to backend. Make sure the backend server is running on port 3002.
            </p>
          </div>
        )}
      </Card>
    </div>
  );
};

export default RealtimeVisualization;
